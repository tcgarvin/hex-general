"""Natural road routing for the world generator (WORLD_SPEC §6, ``road_style="natural"``).

Why the direct roads look ruled: on a hex grid every step is one hex whatever
its heading, but a heading 30° off the three axes can only be walked as a zig-zag
that makes ``cos 30° ≈ 0.87`` hex spacings of headway per step. Least-cost
search over hex steps therefore prices off-axis travel ~15% dearer than travel
along an axis, and between two towns it also has a whole parallelogram of
equally short paths, of which a smooth noise field picks a corner. Both push a
road into long straight legs along the axes joined by 60° corners: a lattice.

Natural routing fixes the metric and then the shape:

1. **Corner-cut pricing** (`natural_path`): the search state carries the heading
   it arrived on, and a hex where the path turns 60° costs only ``cos 30°`` of
   its price, because the drawn road cuts that corner. A zig-zag then costs what
   its true length is, so a road may run at any heading for the same price per
   distance, and the cost field (terrain, valleys, a slow noise) alone decides
   where it bends. Sharp (120°) turns are never taken on new ground, and a
   bridge is crossed along the road ramps baked into its tile (`BRIDGE_RAMPS`).
2. **Smoothing** (`smooth_path`): the hex path is turned into a polyline,
   smoothed, and laid back onto hexes, so the road's turns are spread evenly
   (long sweeps, even zig-zags at any angle) instead of bunched into corners. A
   smoothed stretch is kept only where it is as good as the original: no hex the
   search could not enter, no water, no sharp turn, little extra cost.

Everything is deterministic: plain lists, no set or dict iteration order, ties
broken by index.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence

from app.world.grid import SQRT3, HexGrid

CORNER_SHARE = math.cos(math.pi / 6)
"""Share of a hex's step price paid where a road turns 60° in it (the drawn road
cuts the corner, so its length there is cos 30° of a straight pass)."""

SMOOTH_PASSES = (12, 6, 3)
"""Smoothing strengths tried per free stretch, strongest first (passes of a
1-2-1 filter over the hex-centre polyline; 12 passes spread a turn over ~5
hexes)."""
SMOOTH_SLACK = 0.06
"""How much dearer (share of its price) a smoothed stretch may be than the
searched one it replaces."""

BRIDGE_RAMPS: dict[tuple[int, int], tuple[int, int]] = {
    (2, 5): (0, 4),
    (1, 3): (2, 5),
    (0, 4): (2, 5),
}
"""Bridge tiles by river course (the two directions to the river's neighbours,
ascending) and the two directions their baked road ramps run to. Only these
river shapes have bridge art (``frontend/src/board/tiles/bridge/``: straight_v,
turn_ne_nw, turn_se_sw, mirrors included); any other draws a generic bridge
whose road meets no hex edge."""

_START = 6  # heading of the start state: any first step is allowed
_SAMPLE = 0.15  # polyline sampling step, in hex-radius units (centres are √3 apart)


def bridge_ramps(
    grid: HexGrid, river: Sequence[bool], lake: Sequence[bool]
) -> dict[int, tuple[int, int]]:
    """Every river hex a road may bridge, with its ramp directions.

    A bridge needs a river hex with exactly two river neighbours in a shape that
    has bridge art (`BRIDGE_RAMPS`), no lake beside it and off the map edge (a
    river there runs off the map, which adds a third course edge).
    """
    ramps: dict[int, tuple[int, int]] = {}
    for i in range(grid.size):
        if not river[i] or grid.on_edge(i):
            continue
        around = grid.neighbor_in[i]
        if any(lake[n] for n in around if n >= 0):
            continue
        course = tuple(d for d, n in enumerate(around) if n >= 0 and river[n])
        if len(course) == 2 and course in BRIDGE_RAMPS:
            ramps[i] = BRIDGE_RAMPS[(course[0], course[1])]
    return ramps


def natural_path(
    grid: HexGrid,
    start: int,
    goal: int,
    cost: Callable[[int], float],
    road: Sequence[bool],
    river: Sequence[bool],
    ramps: Mapping[int, tuple[int, int]],
    floor: float,
) -> list[int] | None:
    """Least-cost path ``start`` → ``goal`` under corner-cut pricing, or ``None``.

    ``cost(v)`` prices a hex on the path (``math.inf`` blocks it); ``floor`` is
    the cheapest price, for the heuristic. A hex is charged as the path leaves
    it: its full price going straight, `CORNER_SHARE` of it turning 60°, unless
    it is already ``road`` (existing road keeps its price and may turn any way,
    so a new road can join or follow the network). On new ground a path never
    turns 120°. A bridge hex with bridge art (a key of ``ramps``) is entered
    and left only through its two ramp directions; any other ``river`` hex the
    cost lets it bridge is crossed straight.
    """
    gq, gr = grid.q[goal], grid.r[goal]
    reach = floor * CORNER_SHARE

    def h(i: int) -> float:
        dq = grid.q[i] - gq
        dr = grid.r[i] - gr
        return reach * ((abs(dq) + abs(dr) + abs(dq + dr)) // 2)

    prices: dict[int, float] = {}

    def price(v: int) -> float:
        p = prices.get(v)
        if p is None:
            p = cost(v)
            prices[v] = p
        return p

    neighbor_in = grid.neighbor_in
    start_state = start * 7 + _START
    best: dict[int, float] = {start_state: 0.0}
    came: dict[int, int] = {}
    heap: list[tuple[float, float, int]] = [(h(start), 0.0, start_state)]
    while heap:
        _, g, state = heapq.heappop(heap)
        if g > best[state]:
            continue
        u, heading = divmod(state, 7)
        if u == goal:
            return _trace(came, state)
        on_road = road[u]
        here = price(u)
        exit_ramp = -1
        if u in ramps and heading != _START:
            low, high = ramps[u]
            exit_ramp = high if (heading + 3) % 6 == low else low
        for d in range(6):
            v = neighbor_in[u][d]
            if v < 0:
                continue
            turn = 0 if heading == _START else (d - heading) % 6
            if turn == 3:
                continue  # straight back
            if exit_ramp >= 0 and d != exit_ramp:
                continue  # a bridge is left by its other ramp
            if turn and river[u] and exit_ramp < 0 and heading != _START:
                continue  # a bridge without art is crossed straight
            if turn in (2, 4) and not on_road:
                continue  # a sharp turn on new ground
            if v in ramps and (d + 3) % 6 not in ramps[v]:
                continue  # a bridge is entered by a ramp
            if price(v) == math.inf:
                continue
            charge = here if (turn == 0 or on_road) else CORNER_SHARE * here
            ng = g + charge
            nxt = v * 7 + d
            if ng < best.get(nxt, math.inf):
                best[nxt] = ng
                came[nxt] = state
                heapq.heappush(heap, (ng + h(v), ng, nxt))
    return None


def _trace(came: dict[int, int], state: int) -> list[int]:
    path = [state // 7]
    while state in came:
        state = came[state]
        path.append(state // 7)
    path.reverse()
    return path


def corner_cost(
    grid: HexGrid, path: Sequence[int], cost: Callable[[int], float]
) -> float:
    """Corner-cut price of ``path``'s interior hexes (60° turns at `CORNER_SHARE`);
    ``math.inf`` if one is blocked."""
    total = 0.0
    for k in range(1, len(path) - 1):
        price = cost(path[k])
        turn = _turn(grid, path[k - 1], path[k], path[k + 1])
        total += price if turn == 0 else CORNER_SHARE * price
    return total


def _turn(grid: HexGrid, a: int, b: int, c: int) -> int:
    """Hex turn at ``b`` on ``a → b → c``: 0 straight, 1 gentle (60°), 2 sharp."""
    din = grid.neighbor_in[a].index(b)
    dout = grid.neighbor_in[b].index(c)
    gap = (dout - din) % 6
    return min(gap, 6 - gap)


def smooth_path(
    grid: HexGrid,
    path: list[int],
    fixed: Sequence[bool],
    cost: Callable[[int], float],
    water: Sequence[bool],
) -> list[int]:
    """``path`` with each free stretch between ``fixed`` hexes smoothed.

    The path's ends, its ``fixed`` hexes (pass existing road), its ``water``
    hexes (bridges) and the hexes either side of each bridge stay put. Each
    stretch between them is smoothed as a polyline (`SMOOTH_PASSES`, strongest
    first) and laid back onto hexes; the first result that enters no blocked hex
    and no ``water``, turns no sharp corner on new ground (where it meets the
    rest of the path included) and costs at most `SMOOTH_SLACK` more replaces
    the stretch; otherwise the stretch is kept as searched.
    """
    last = len(path) - 1
    anchors = [
        k
        for k in range(len(path))
        if k in (0, last)
        or fixed[path[k]]
        or any(water[path[j]] for j in (k - 1, k, k + 1) if 0 <= j <= last)
    ]
    out: list[int] = [path[0]]
    for a, b in zip(anchors, anchors[1:], strict=False):
        stretch = path[a : b + 1]
        if b - a >= 3:
            before = path[a - 1] if a > 0 and not fixed[path[a]] else -1
            after = path[b + 1] if b < last and not fixed[path[b]] else -1
            stretch = _smooth_stretch(grid, stretch, before, after, cost, water)
        out.extend(stretch[1:])
    return out


def _smooth_stretch(
    grid: HexGrid,
    stretch: list[int],
    before: int,
    after: int,
    cost: Callable[[int], float],
    water: Sequence[bool],
) -> list[int]:
    """``stretch`` smoothed (see `smooth_path`). ``before`` and ``after`` are the
    path's hexes either side of it where the joint is new ground and so may not
    turn sharply (``-1`` where it may: the path's end, or existing road)."""
    limit = corner_cost(grid, stretch, cost) * (1.0 + SMOOTH_SLACK)
    xs = [grid.px[i] for i in stretch]
    ys = [grid.py[i] for i in stretch]
    done = 0
    for passes in SMOOTH_PASSES:
        while done < passes:
            xs = _relax(xs)
            ys = _relax(ys)
            done += 1
        candidate = hex_line(grid, list(zip(xs, ys, strict=True)))
        if candidate[-1] != stretch[-1] or any(water[i] for i in candidate):
            continue
        chain = [before, *candidate, after]
        lo = 1 if before < 0 else 0
        hi = len(chain) - (1 if after < 0 else 0)
        if any(
            _turn(grid, chain[k - 1], chain[k], chain[k + 1]) == 2
            for k in range(lo + 1, hi - 1)
        ):
            continue
        if corner_cost(grid, candidate, cost) <= limit:
            return candidate
    return stretch


def _relax(values: list[float]) -> list[float]:
    """One 1-2-1 smoothing pass with both ends held."""
    out = list(values)
    for k in range(1, len(values) - 1):
        out[k] = 0.25 * values[k - 1] + 0.5 * values[k] + 0.25 * values[k + 1]
    return out


def hex_line(grid: HexGrid, points: Sequence[tuple[float, float]]) -> list[int]:
    """The hexes a pixel-space polyline passes through, as a chain of neighbours.

    Points are in ``grid.px``/``grid.py`` units. The polyline is sampled densely
    and each sample mapped to its hex; where two successive hexes are not
    neighbours (the line slipped through a corner), the common neighbour nearest
    the line between them is inserted. A hex the chain returns to cuts the loop
    out. Off-map samples are a caller bug and raise `ValueError`.
    """
    chain: list[int] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / _SAMPLE))
        for k in range(n + 1):
            t = k / n
            i = pixel_to_hex(grid, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if i < 0:
                raise ValueError(f"polyline leaves the map near ({x0:.1f}, {y0:.1f})")
            if chain and chain[-1] == i:
                continue
            if chain and i not in grid.neighbors[chain[-1]]:
                chain.append(_bridge_gap(grid, chain[-1], i))
            if i in chain:
                del chain[chain.index(i) + 1 :]
                continue
            chain.append(i)
    return chain


def _bridge_gap(grid: HexGrid, a: int, b: int) -> int:
    """The common neighbour of ``a`` and ``b`` (two apart) nearest their midpoint."""
    mx = (grid.px[a] + grid.px[b]) / 2
    my = (grid.py[a] + grid.py[b]) / 2
    common = [n for n in grid.neighbors[a] if n in grid.neighbors[b]]
    return min(common, key=lambda n: (math.hypot(grid.px[n] - mx, grid.py[n] - my), n))


def pixel_to_hex(grid: HexGrid, x: float, y: float) -> int:
    """Index of the hex containing pixel-space point ``(x, y)`` (``-1`` off-map)."""
    q = x / 1.5
    r = y / SQRT3 - q / 2.0
    s = -q - r
    rq, rr, rs = round(q), round(r), round(s)
    dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
    if dq > dr and dq > ds:
        rq = -rr - rs
    elif dr > ds:
        rr = -rq - rs
    return grid.index_of(int(rq), int(rr))
