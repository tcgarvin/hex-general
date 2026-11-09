"""Flat-index hex grid geometry and noise for world generation (WORLD_SPEC §6).

The generator works on a rectangle of ``width × height`` hexes in authored
odd-q offset layout (the same layout `app.engine.scenario` loads), addressed by a
flat index ``i = row * width + col``. Plain ints and lists keep the generator's
hot loops fast without numpy; the result converts to engine `Axial` coords only
once, at the end.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import deque
from collections.abc import Callable, Iterable

from app.engine.hex_coords import AXIAL_DIRECTIONS

SQRT3 = math.sqrt(3.0)
_DIRECTIONS: tuple[tuple[int, int], ...] = tuple((d.q, d.r) for d in AXIAL_DIRECTIONS)


class HexGrid:
    """Geometry and adjacency for a ``width × height`` odd-q flat-top rectangle.

    ``q``/``r`` are axial coordinates per index; ``px``/``py`` the hex centre in
    unit-radius pixel space (for noise and centroids); ``neighbors`` lists on-map
    neighbours; ``neighbor_in`` gives the neighbour per direction (``-1`` off-map).
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.size = width * height
        self.col = [i % width for i in range(self.size)]
        self.row = [i // width for i in range(self.size)]
        self.q = list(self.col)
        self.r = [row - (col - (col & 1)) // 2 for col, row in zip(self.col, self.row)]
        self.px = [1.5 * col for col in self.col]
        self.py = [
            SQRT3 * (row + 0.5 * (col & 1)) for col, row in zip(self.col, self.row)
        ]
        self.neighbor_in: list[tuple[int, ...]] = []
        self.neighbors: list[tuple[int, ...]] = []
        for i in range(self.size):
            per_dir = tuple(
                self.index_of(self.q[i] + dq, self.r[i] + dr) for dq, dr in _DIRECTIONS
            )
            self.neighbor_in.append(per_dir)
            self.neighbors.append(tuple(n for n in per_dir if n >= 0))
        # Pixel offset of each direction, for steering rivers inland.
        self.direction_px = [
            (1.5 * dq, SQRT3 * (dr + dq / 2.0)) for dq, dr in _DIRECTIONS
        ]

    def index_of(self, q: int, r: int) -> int:
        """Flat index of axial ``(q, r)``, or ``-1`` if it is off the rectangle."""
        col = q
        row = r + (q - (q & 1)) // 2
        if 0 <= col < self.width and 0 <= row < self.height:
            return row * self.width + col
        return -1

    def distance(self, a: int, b: int) -> int:
        """Hex distance between two indices."""
        dq = self.q[a] - self.q[b]
        dr = self.r[a] - self.r[b]
        return (abs(dq) + abs(dr) + abs(dq + dr)) // 2

    def on_edge(self, i: int) -> bool:
        """Whether index ``i`` lies on the rectangle's border."""
        return self.col[i] in (0, self.width - 1) or self.row[i] in (0, self.height - 1)

    def window(self, center: int, radius: int) -> Iterable[int]:
        """Indices within a ``radius``-wide offset square around ``center``."""
        c, r = self.col[center], self.row[center]
        for row in range(max(0, r - radius), min(self.height, r + radius + 1)):
            base = row * self.width
            for col in range(max(0, c - radius), min(self.width, c + radius + 1)):
                yield base + col


def fbm(
    grid: HexGrid, rng: random.Random, feature: float, octaves: int = 4
) -> list[float]:
    """Smooth value-noise fBm at hex centres; ``feature`` is the base wavelength."""
    total = [0.0] * grid.size
    amp, norm = 1.0, 0.0
    max_x = max(grid.px)
    max_y = max(grid.py)
    for octave in range(octaves):
        scale = feature / (2**octave)
        cols = int(max_x / scale) + 3
        rows = int(max_y / scale) + 3
        lattice = [[rng.random() for _ in range(cols)] for _ in range(rows)]
        for i in range(grid.size):
            fx = grid.px[i] / scale
            fy = grid.py[i] / scale
            x0 = int(fx)
            y0 = int(fy)
            tx = fx - x0
            ty = fy - y0
            tx = tx * tx * (3 - 2 * tx)
            ty = ty * ty * (3 - 2 * ty)
            top = lattice[y0][x0] * (1 - tx) + lattice[y0][x0 + 1] * tx
            bottom = lattice[y0 + 1][x0] * (1 - tx) + lattice[y0 + 1][x0 + 1] * tx
            total[i] += amp * (top * (1 - ty) + bottom * ty)
        norm += amp
        amp *= 0.5
    return [t / norm for t in total]


def multi_source_bfs(
    grid: HexGrid, sources: list[int], passable: list[bool]
) -> tuple[list[int], list[int]]:
    """Nearest source (by steps over passable hexes) and that distance, per index.

    Ties go to the source listed first. Unreachable or impassable hexes get
    source ``-1`` and distance ``-1``.
    """
    owner = [-1] * grid.size
    dist = [-1] * grid.size
    queue: deque[int] = deque()
    for k, s in enumerate(sources):
        if owner[s] == -1:
            owner[s] = k
            dist[s] = 0
            queue.append(s)
    while queue:
        u = queue.popleft()
        for v in grid.neighbors[u]:
            if owner[v] == -1 and passable[v]:
                owner[v] = owner[u]
                dist[v] = dist[u] + 1
                queue.append(v)
    return owner, dist


def multi_source_dijkstra(
    grid: HexGrid, sources: list[int], entry_cost: list[int]
) -> list[int]:
    """Nearest source by summed entry cost; ``entry_cost[i] <= 0`` marks impassable.

    Returns the source position per index (``-1`` if unreachable). Ties break by
    lower cost, then lower source position, then lower index: deterministic.
    """
    owner = [-1] * grid.size
    best = [math.inf] * grid.size
    heap: list[tuple[float, int, int]] = []
    for k, s in enumerate(sources):
        best[s] = 0
        heapq.heappush(heap, (0, k, s))
    while heap:
        g, k, u = heapq.heappop(heap)
        if owner[u] != -1:
            continue
        owner[u] = k
        for v in grid.neighbors[u]:
            cost = entry_cost[v]
            if cost <= 0 or owner[v] != -1:
                continue
            ng = g + cost
            if ng < best[v]:
                best[v] = ng
                heapq.heappush(heap, (ng, k, v))
    return owner


def astar(
    grid: HexGrid,
    start: int,
    goal: int,
    cost: Callable[[int], float],
    floor: float,
    allowed: Callable[[int], bool] | None = None,
) -> list[int] | None:
    """Least-cost index path ``start`` → ``goal`` (both included), or ``None``.

    ``cost(v)`` is the price of entering ``v`` (``math.inf`` blocks it); ``floor``
    is the cheapest possible entry cost, for an admissible heuristic. ``allowed``
    optionally restricts the hexes the path may enter.
    """
    gq, gr = grid.q[goal], grid.r[goal]

    def h(i: int) -> float:
        dq = grid.q[i] - gq
        dr = grid.r[i] - gr
        return floor * ((abs(dq) + abs(dr) + abs(dq + dr)) // 2)

    best = {start: 0.0}
    came: dict[int, int] = {}
    heap: list[tuple[float, float, int]] = [(h(start), 0.0, start)]
    while heap:
        _, g, u = heapq.heappop(heap)
        if u == goal:
            path = [goal]
            while path[-1] != start:
                path.append(came[path[-1]])
            path.reverse()
            return path
        if g > best[u]:
            continue
        for v in grid.neighbors[u]:
            if allowed is not None and not allowed(v):
                continue
            step = cost(v)
            if step == math.inf:
                continue
            ng = g + step
            if ng < best.get(v, math.inf):
                best[v] = ng
                came[v] = u
                heapq.heappush(heap, (ng + h(v), ng, v))
    return None
