"""The world generator's terrain stage (WORLD_SPEC §6): hills, lakes, rivers, woods.

Terrain is built from sized objects rather than thresholded noise, so features
stay small and legible and the map gets regional character:

1. **Highlands:** a broad noise field whose mid-contour band is hill country, so
   rugged ground forms belts; each hex also gets the hex axis along the belt.
2. **Hills:** ridges, short walks along that axis (sometimes two hexes wide, with
   a pass now and then), seeded in proportion to ruggedness⁴. Separate ridges
   never touch.
3. **Lakes:** ragged blobs of 3–22 hexes on low ground, kept off the map edge,
   apart from each other and off hills.
4. **Rivers:** a drainage tree grown in from the map edge. Lakes and springs at
   the foot of hills are sources; every course ends on the map edge (flowing off
   it), in a lake that already drains, or at a clean Y junction on a river that does, so all
   water drains off the map. Each lake gets one outflow (a lake none can drain
   is removed); springs add tributaries up to the river share. Courses are one
   hex wide, never turn more than 60° a step, touch no other water but where
   they end, and never wall off a small patch of land.
5. **Woods:** blobs from copses to forests, two open hexes apart, seeded more in
   wooded regions, beside hills and along some banks; deep forest gets glades.

`build_land` runs the stages on one `random.Random` and returns a frozen `Land`.
"""

from __future__ import annotations

import heapq
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.engine.stats import Terrain
from app.world.grid import HexGrid, depth, fbm, rank_normalise

SizeMix = tuple[tuple[tuple[int, int], float], ...]

HILL_KINDS: SizeMix = (
    ((1, 2), 0.12),
    ((4, 7), 0.43),
    ((8, 12), 0.32),
    ((13, 18), 0.13),
)
"""Ridge length ranges (hexes walked) and how often each is drawn."""
HILL_SEED_FLOOR = 0.012
"""Ridge seed weight anywhere, on top of ruggedness⁴, so lowlands get the odd knoll."""
HILL_ALONG_GRAIN = 0.8
"""Chance a ridge runs along its region's grain rather than a random axis."""
HILL_PASS_CHANCE = 0.22
"""Chance per step, after four solid hexes, that a ridge leaves a gap (a pass)."""
HILL_MAX_MISSES = 4000
"""Ridge seeds that may land on taken ground before the hills share is given up on."""

LAKE_SIZES: SizeMix = (
    ((3, 5), 0.32),
    ((6, 10), 0.40),
    ((11, 16), 0.20),
    ((17, 22), 0.08),
)
"""Lake size ranges (hexes, before holes are filled) and how often each is drawn."""
LAKE_EDGE_MARGIN = 3
"""Rows or columns kept between any lake hex and the map border."""
LAKE_GAP = 5
"""Lakes keep more than this many hexes apart."""
LAKE_SITE_TRIES = 24
"""Candidate centres per lake in best-candidate placement."""
RIVER_FLOOR = 0.35
"""Cheapest step cost of a river course (the routing heuristic's unit)."""
RIVER_MAX_LEN = 80
"""Longest spring course, in hexes."""
LAKE_OUTFLOW_REACH = 3 * RIVER_MAX_LEN
"""Longest lake outflow aimed at the nearest drained water."""
LAKE_OUTFLOW_FALLBACK_REACH = 6 * RIVER_MAX_LEN
"""Longest lake outflow aimed at the map edge, tried if the first attempt fails."""
RIVER_MIN_LEN = 5
"""Shortest course that may end anywhere but the map edge."""
SPRING_MIN_LEN = 8
"""Shortest course a spring may lay."""
SPRING_SPACING = 12
"""Springs keep at least this many hexes apart."""
SPRING_DRY_RADIUS = 4
"""A spring needs no water within this many hexes."""
SPRING_EDGE_MARGIN = 3
"""Rows or columns kept between a spring and the map border."""
RIVER_STRAIGHT_PENALTY = 0.7
"""Extra cost of a second straight step in a row, so courses meander."""
POCKET_MIN = 40
"""A course may not wall off a patch of land smaller than this (hexes)."""

WOOD_KINDS: SizeMix = (
    ((2, 3), 0.18),
    ((5, 9), 0.40),
    ((10, 18), 0.30),
    ((19, 30), 0.12),
)
"""Wood sizes (hexes), copse to forest, and how often each is drawn."""
WOOD_GAP = 2
"""Open hexes kept between separate woods."""
WOOD_MAX_MISSES = 6000
"""Wood seeds that may land on taken ground before the woods share is given up on."""
GLADE_DEPTH = 3
"""Woods hexes this deep inside a forest are opened as glades."""

_TO_EDGE = -1  # a river goal: the map edge rather than a hex of drained water


@dataclass(frozen=True)
class Land:
    """What the terrain stage decides, per hex index.

    ``terrain`` is plains, woods, hills, river or lake. ``river`` marks river
    course hexes, ``lake`` lake hexes and ``water`` either. ``ruggedness`` (in
    [0, 1]) is the regional hill-country field, which also stands in for
    elevation. ``lakes`` and ``rivers`` count the lakes and river courses;
    ``lakes_dropped`` the lakes removed because no outflow could drain them.
    ``spring_hills`` marks the hills beside each spring; later stages keep them
    hills, so every river that does not leave a lake visibly rises by hills.
    """

    terrain: tuple[Terrain, ...]
    river: tuple[bool, ...]
    lake: tuple[bool, ...]
    water: tuple[bool, ...]
    ruggedness: tuple[float, ...]
    lakes: int
    lakes_dropped: int
    rivers: int
    spring_hills: tuple[bool, ...]


def build_land(
    grid: HexGrid,
    rng: random.Random,
    *,
    hills_share: float,
    woods_share: float,
    river_share: float,
    hexes_per_lake: int,
) -> Land:
    """Lay hills, lakes, rivers and woods on ``grid`` (deterministic in ``rng``).

    Every lake and river drains off the map: following water downstream always
    reaches the map edge. The shares are targets: hills and woods stop short when
    seeds keep landing on taken ground, rivers when no more springs fit.
    """
    ruggedness, grain = _highlands(grid, rng)
    hills = _hills(grid, rng, ruggedness, grain, hills_share)
    lake_bodies = _lakes(grid, rng, ruggedness, hills, hexes_per_lake)
    for body in lake_bodies:
        for i in body:
            hills[i] = False  # a filled hole may have been a hill hex
    net = _RiverNet(grid, _river_costs(grid, rng, ruggedness, hills), lake_bodies)
    lakes_dropped = _drain_lakes(net, hills)
    springs = _springs(net, rng, hills, round(river_share * grid.size))
    spring_hills = [False] * grid.size
    for spring in springs:
        for n in grid.neighbors[spring]:
            spring_hills[n] = hills[n]
    water = [net.water(i) for i in range(grid.size)]
    woods = _woods(grid, rng, ruggedness, hills, water, woods_share)
    terrain = [
        _terrain_of(net.lake[i], net.river[i], hills[i], woods[i])
        for i in range(grid.size)
    ]
    return Land(
        terrain=tuple(terrain),
        river=tuple(net.river),
        lake=tuple(net.lake),
        water=tuple(water),
        ruggedness=tuple(ruggedness),
        lakes=len(lake_bodies) - lakes_dropped,
        lakes_dropped=lakes_dropped,
        rivers=len(net.courses),
        spring_hills=tuple(spring_hills),
    )


def _terrain_of(lake: bool, river: bool, hills: bool, woods: bool) -> Terrain:
    """A hex's terrain from the stage masks (they never overlap but for woods,
    which never grow on water or hills)."""
    if lake:
        return Terrain.LAKE
    if river:
        return Terrain.RIVER
    if hills:
        return Terrain.HILLS
    return Terrain.WOODS if woods else Terrain.PLAINS


def _draw_size(rng: random.Random, kinds: SizeMix) -> int:
    """A size drawn from a size mix: pick a range by weight, then a size in it."""
    ((low, high),) = rng.choices([k for k, _ in kinds], weights=[w for _, w in kinds])
    return rng.randint(low, high)


def _weighted_pick(
    rng: random.Random, pool: Sequence[int], weight: Sequence[float], top: float
) -> int:
    """A hex from ``pool`` drawn in proportion to ``weight`` (rejection sampling;
    ``top`` is the largest weight)."""
    while True:
        candidate = rng.choice(pool)
        if rng.random() * top <= weight[candidate]:
            return candidate


def _grow_blob(
    grid: HexGrid,
    seed: int,
    size: int,
    priority: Callable[[int], float],
    may_take: Callable[[int], bool],
) -> list[int]:
    """Grow a blob of up to ``size`` hexes from ``seed``, always taking the
    frontier hex of lowest ``priority`` (ties to the lower index)."""
    body = [seed]
    inside = {seed}
    frontier: dict[int, float] = {}
    current = seed
    while True:
        for n in grid.neighbors[current]:
            if n not in inside and n not in frontier and may_take(n):
                frontier[n] = priority(n)
        if len(body) >= size or not frontier:
            return body
        current = min(frontier, key=lambda i: (frontier[i], i))
        del frontier[current]
        body.append(current)
        inside.add(current)


# --- Highlands and hills ----------------------------------------------------------


def _highlands(grid: HexGrid, rng: random.Random) -> tuple[list[float], list[int]]:
    """Regional ruggedness in [0, 1], and per hex the axis (0–2) ridges run along.

    Hill country is the band around the mid contour of a broad noise field, so it
    snakes across the map as ranges rather than sitting in round blobs; ridges
    run along the contour.
    """
    base = rank_normalise(fbm(grid, rng, feature=170.0, octaves=2))
    detail = rank_normalise(fbm(grid, rng, feature=60.0, octaves=2))
    ruggedness = rank_normalise(
        [0.75 * (1.0 - abs(2.0 * b - 1.0)) + 0.25 * d for b, d in zip(base, detail)]
    )
    grain = [0] * grid.size
    for i in range(grid.size):
        gx = gy = 0.0
        for n in grid.neighbors[i]:
            rise = base[n] - base[i]
            gx += rise * (grid.px[n] - grid.px[i])
            gy += rise * (grid.py[n] - grid.py[i])
        along_x, along_y = -gy, gx  # perpendicular to the slope: along the contour
        grain[i] = max(
            range(3),
            key=lambda k: abs(
                along_x * grid.direction_px[k][0] + along_y * grid.direction_px[k][1]
            ),
        )
    return ruggedness, grain


def _hills(
    grid: HexGrid,
    rng: random.Random,
    ruggedness: list[float],
    grain: list[int],
    share: float,
) -> list[bool]:
    """Ridges up to ``share`` of the map, densest where the region is rugged.

    A hex next to a placed ridge is never taken by another, so no hill mass is
    bigger than one ridge.
    """
    weight = [HILL_SEED_FLOOR + r**4 for r in ruggedness]
    top = max(weight)
    pool = [i for i in range(grid.size) if not grid.on_edge(i)]
    hills = [False] * grid.size
    near_hills = [False] * grid.size  # a hill hex or next to one

    def is_free(i: int) -> bool:
        return not grid.on_edge(i) and not near_hills[i]

    target = round(share * grid.size)
    placed = misses = 0
    while placed < target and misses < HILL_MAX_MISSES:
        seed = _weighted_pick(rng, pool, weight, top)
        if not is_free(seed):
            misses += 1
            continue
        axis = grain[seed] if rng.random() < HILL_ALONG_GRAIN else rng.randrange(3)
        thickness = 0.25 + 0.35 * ruggedness[seed]
        ridge = _grow_ridge(grid, rng, seed, axis, thickness, is_free)
        for i in ridge:
            hills[i] = True
            near_hills[i] = True
            for n in grid.neighbors[i]:
                near_hills[n] = True
        placed += len(ridge)
    return hills


def _grow_ridge(
    grid: HexGrid,
    rng: random.Random,
    seed: int,
    axis: int,
    thickness: float,
    is_free: Callable[[int], bool],
) -> list[int]:
    """Walk a ridge from ``seed`` along ``axis``, wobbling at most one direction off
    it; ``thickness`` is the chance per step of a second hex alongside."""
    length = _draw_size(rng, HILL_KINDS)
    heading = axis + (3 if rng.random() < 0.5 else 0)
    ridge = [seed]
    current = seed
    direction = heading
    steps = 1
    since_pass = 0
    while steps < length:
        roll = rng.random()
        turn = 0 if roll < 0.55 else (1 if roll < 0.775 else -1)
        step_dir = (direction + turn) % 6
        if (step_dir - heading) % 6 not in (0, 1, 5):
            step_dir = direction
        nxt = grid.neighbor_in[current][step_dir]
        if nxt < 0 or not is_free(nxt):
            break
        direction = step_dir
        steps += 1
        since_pass += 1
        current = nxt
        if since_pass >= 4 and steps < length - 1 and rng.random() < HILL_PASS_CHANCE:
            since_pass = 0
            continue  # leave this hex open: a pass through the ridge
        ridge.append(nxt)
        if rng.random() < thickness:
            side = grid.neighbor_in[nxt][(step_dir + rng.choice((2, 4))) % 6]
            if side >= 0 and is_free(side) and side not in ridge:
                ridge.append(side)
    return ridge


# --- Lakes ------------------------------------------------------------------------


def _lakes(
    grid: HexGrid,
    rng: random.Random,
    elevation: list[float],
    hills: list[bool],
    hexes_per_lake: int,
) -> list[list[int]]:
    """About one lake per ``hexes_per_lake`` hexes, each a list of hex indices.

    Centres are best-candidate picks (far from other lakes, on low ground); each
    lake grows raggedly by a noise field, keeps `LAKE_EDGE_MARGIN` from the map
    border and more than `LAKE_GAP` from other lakes, avoids hills, and has its
    holes filled so it never rings an island.
    """
    count = max(1, round(grid.size / hexes_per_lake))
    shape = fbm(grid, rng, feature=6.0, octaves=2)
    near_lake = [False] * grid.size  # within LAKE_GAP of a lake

    def in_margin(i: int) -> bool:
        return grid.edge_distance(i) >= LAKE_EDGE_MARGIN

    pool = [i for i in range(grid.size) if in_margin(i) and not hills[i]]
    centers: list[int] = []
    lakes: list[list[int]] = []
    for _ in range(count):
        center = _lake_center(grid, rng, pool, centers, near_lake, elevation)
        if center < 0:
            continue

        def priority(i: int, center: int = center) -> float:
            return shape[i] + 0.035 * grid.distance(i, center)

        def may_take(i: int) -> bool:
            return not hills[i] and not near_lake[i] and in_margin(i)

        body = _grow_blob(grid, center, _draw_size(rng, LAKE_SIZES), priority, may_take)
        body = _fill_holes(grid, body)
        if len(body) < 3:
            continue
        centers.append(center)
        lakes.append(body)
        for i in body:
            for j in grid.disc(i, LAKE_GAP):
                near_lake[j] = True
    return lakes


def _lake_center(
    grid: HexGrid,
    rng: random.Random,
    pool: list[int],
    centers: list[int],
    near_lake: list[bool],
    elevation: list[float],
) -> int:
    """The best of `LAKE_SITE_TRIES` random centres, scoring distance from other
    lakes (capped at 60) times lowness; ``-1`` if every try was too near a lake."""
    best, best_score = -1, -1.0
    for _ in range(LAKE_SITE_TRIES):
        candidate = rng.choice(pool)
        if near_lake[candidate]:
            continue
        spread = min((grid.distance(candidate, c) for c in centers), default=60)
        score = min(spread, 60) * (1.25 - elevation[candidate])
        if score > best_score:
            best, best_score = candidate, score
    return best


def _fill_holes(grid: HexGrid, body: list[int]) -> list[int]:
    """``body`` plus every hex it encloses (in its bounding box, cut off from the
    box's rim by lake hexes)."""
    inside = [False] * grid.size
    for i in body:
        inside[i] = True
    col0 = min(grid.col[i] for i in body) - 1
    col1 = max(grid.col[i] for i in body) + 1
    row0 = min(grid.row[i] for i in body) - 1
    row1 = max(grid.row[i] for i in body) + 1
    box = [
        row * grid.width + col
        for row in range(row0, row1 + 1)
        for col in range(col0, col1 + 1)
    ]

    def in_box(i: int) -> bool:
        return col0 <= grid.col[i] <= col1 and row0 <= grid.row[i] <= row1

    reached = [False] * grid.size
    stack = [
        i for i in box if grid.col[i] in (col0, col1) or grid.row[i] in (row0, row1)
    ]
    for i in stack:
        reached[i] = True
    while stack:
        u = stack.pop()
        for v in grid.neighbors[u]:
            if in_box(v) and not inside[v] and not reached[v]:
                reached[v] = True
                stack.append(v)
    return body + [i for i in box if not inside[i] and not reached[i]]


# --- Rivers -----------------------------------------------------------------------


def _river_costs(
    grid: HexGrid, rng: random.Random, elevation: list[float], hills: list[bool]
) -> list[float]:
    """Step cost of a river course per hex: cheap on low ground and along the mid
    contour of a meander field (so courses wind), with per-hex jitter; hills are
    closed to rivers."""
    meander = rank_normalise(fbm(grid, rng, feature=16.0, octaves=2))
    return [
        (
            math.inf
            if hills[i]
            else RIVER_FLOOR
            + 0.8 * elevation[i]
            + 1.6 * abs(2.0 * meander[i] - 1.0)
            + 2.4 * rng.random() ** 1.5
        )
        for i in range(grid.size)
    ]


_State = tuple[int, int, int]  # (hex, heading, last turn: -1, 0 or 1)


class _RiverNet:
    """The water laid so far, and the rules a new river course must follow.

    Water hexes belong to *systems*: lake ``k`` is system ``k``, the map edge is
    system `sea`, and each river course takes its source lake's system or a new
    one. A union-find over systems records which are joined; a system joined to
    the sea is *drained*, and a course may only end in drained water, so every
    course drains off the map and none closes a loop.
    """

    def __init__(
        self, grid: HexGrid, cost: list[float], lakes: list[list[int]]
    ) -> None:
        self.grid = grid
        self.cost = cost
        self.lakes = lakes
        self.dropped = [False] * len(lakes)
        self.lake = [False] * grid.size
        self.river = [False] * grid.size
        self.links = [0] * grid.size  # river hex: river hexes it is joined to
        self.system = [-1] * grid.size  # water hex: its system
        self.wet_neighbors = [0] * grid.size  # adjacent water hexes
        self.parent = list(range(len(lakes) + 1))
        self.sea = len(lakes)
        self.courses: list[list[int]] = []
        self.river_hexes = 0
        self.centers = [_center_hex(grid, body) for body in lakes]
        for k, body in enumerate(lakes):
            for i in body:
                self.lake[i] = True
                self.system[i] = k
                for n in grid.neighbors[i]:
                    self.wet_neighbors[n] += 1

    def find(self, system: int) -> int:
        """The root of ``system``'s joined group."""
        while self.parent[system] != system:
            self.parent[system] = self.parent[self.parent[system]]
            system = self.parent[system]
        return system

    def new_system(self) -> int:
        """A fresh system id for a course that does not leave a lake."""
        self.parent.append(len(self.parent))
        return len(self.parent) - 1

    def water(self, i: int) -> bool:
        """Whether hex ``i`` is river or lake."""
        return self.river[i] or self.lake[i]

    def drained(self, system: int) -> bool:
        """Whether ``system`` already drains off the map."""
        return self.find(system) == self.find(self.sea)

    def remove_lake(self, k: int) -> None:
        """Return undrained lake ``k``'s hexes to land."""
        self.dropped[k] = True
        for i in self.lakes[k]:
            self.lake[i] = False
            self.system[i] = -1
            for n in self.grid.neighbors[i]:
                self.wet_neighbors[n] -= 1

    def route(
        self, starts: list[tuple[int, int]], goal: int, max_len: int
    ) -> tuple[list[int], int] | None:
        """Least-cost course of at most ``max_len`` hexes from one of ``starts``
        (hex, heading) to a place it may end (`_end_system`), with the drained
        system it joins there; ``None`` if there is none.

        ``goal`` (a hex of drained water, or `_TO_EDGE`) only steers the search;
        the course ends at the first place it may. Search state is (hex, heading,
        last turn): a course turns at most 60° a step, never twice running the
        same way, and pays a little for two straight steps in a row. No hex but
        the last may touch water (bar the first, which may touch its own lake).
        """
        grid = self.grid

        def heuristic(i: int) -> float:
            if goal == _TO_EDGE:
                return RIVER_FLOOR * grid.edge_distance(i)
            # A course ends beside the water, a few hexes short of a lake centre.
            return RIVER_FLOOR * max(0, grid.distance(i, goal) - 4)

        best: dict[_State, float] = {}
        came: dict[_State, _State] = {}
        length: dict[_State, int] = {}
        heap: list[tuple[float, float, _State]] = []
        for hex_, heading in starts:
            state = (hex_, heading, 0)
            best[state] = self.cost[hex_]
            length[state] = 1
            heapq.heappush(heap, (best[state] + heuristic(hex_), best[state], state))
        while heap:
            _, spent, state = heapq.heappop(heap)
            if spent > best[state]:
                continue
            i, heading, last_turn = state
            steps = length[state]
            if steps >= RIVER_MIN_LEN:
                joined = self._end_system(i, heading)
                if joined is not None:
                    return _trace(came, state), joined
            if steps >= max_len or (steps > 1 and self.wet_neighbors[i] > 0):
                continue  # too long, or touching water where it may not end
            for turn in (0, 1, -1):
                if turn != 0 and turn == last_turn:
                    continue
                step_dir = (heading + turn) % 6
                v = grid.neighbor_in[i][step_dir]
                if v < 0 or self.water(v):
                    continue
                nxt = (v, step_dir, turn)
                price = spent + self.cost[v]
                if turn == 0 and last_turn == 0:
                    price += RIVER_STRAIGHT_PENALTY
                if price < best.get(nxt, math.inf):
                    best[nxt] = price
                    came[nxt] = state
                    length[nxt] = steps + 1
                    heapq.heappush(heap, (price + heuristic(v), price, nxt))
        return None

    def _end_system(self, i: int, heading: int) -> int | None:
        """The drained system a course heading ``heading`` joins if it ends at
        ``i``, or ``None`` if it may not end there.

        A course may end on the map edge touching no water (it flows off the
        map); beside one drained lake and no other water; or beside exactly one
        hex of a drained river, off the edge and not already a junction, for a
        clean Y. The water it meets must lie straight ahead or a gentle turn
        away, so the last hex never makes a sharp turn.
        """
        grid = self.grid
        if self.wet_neighbors[i] == 0:
            return self.sea if grid.on_edge(i) else None
        wet = [n for n in grid.neighbors[i] if self.water(n)]
        lakes_met = sorted({self.system[n] for n in wet if self.lake[n]})
        rivers_met = [n for n in wet if self.river[n]]
        if lakes_met and rivers_met:
            return None
        ahead = [grid.neighbor_in[i][(heading + t) % 6] for t in (0, 1, -1)]
        if lakes_met:
            if len(lakes_met) != 1 or not self.drained(lakes_met[0]):
                return None
            lake_id = lakes_met[0]
            if any(
                a >= 0 and self.lake[a] and self.system[a] == lake_id for a in ahead
            ):
                return lake_id
            return None
        if len(rivers_met) == 1:
            branch = rivers_met[0]
            if (
                branch in ahead
                and self.drained(self.system[branch])
                and self.links[branch] <= 2
                and not grid.on_edge(branch)
            ):
                return self.system[branch]
        return None

    def touches_itself(self, course: list[int]) -> bool:
        """Whether any hex of ``course`` touches a hex of it other than its
        neighbours in line."""
        position = {i: k for k, i in enumerate(course)}
        for k, i in enumerate(course):
            for n in self.grid.neighbors[i]:
                j = position.get(n)
                if j is not None and abs(j - k) > 1:
                    return True
        return False

    def walls_off_land(self, course: list[int]) -> bool:
        """Whether laying ``course`` would cut off a patch of land smaller than
        `POCKET_MIN` hexes (bounded flood fills from the land beside it)."""
        grid = self.grid
        blocked = set(course)
        cleared: set[int] = set()
        for i in course:
            for start in grid.neighbors[i]:
                if start in blocked or start in cleared or self.water(start):
                    continue
                seen = {start}
                stack = [start]
                while stack and len(seen) <= POCKET_MIN:
                    u = stack.pop()
                    for v in grid.neighbors[u]:
                        if v not in seen and v not in blocked and not self.water(v):
                            seen.add(v)
                            stack.append(v)
                if len(seen) <= POCKET_MIN:
                    return True
                cleared |= seen
        return False

    def commit(self, course: list[int], own: int, joined: int) -> None:
        """Lay ``course`` as system ``own``, joined to system ``joined`` at its end."""
        grid = self.grid
        for k, i in enumerate(course):
            self.river[i] = True
            self.system[i] = own
            self.links[i] = (k > 0) + (k < len(course) - 1)
            for n in grid.neighbors[i]:
                self.wet_neighbors[n] += 1
        last = course[-1]
        before_last = course[-2] if len(course) > 1 else -1
        for n in grid.neighbors[last]:
            if self.river[n] and n != before_last and self.system[n] != own:
                self.links[n] += 1
                self.links[last] += 1
        self.parent[self.find(own)] = self.find(joined)
        self.courses.append(course)
        self.river_hexes += len(course)


def _trace(came: dict[_State, _State], state: _State) -> list[int]:
    """The hexes of the search path ending at ``state``, first to last."""
    path = [state[0]]
    while state in came:
        state = came[state]
        path.append(state[0])
    path.reverse()
    return path


def _center_hex(grid: HexGrid, body: list[int]) -> int:
    """The hex of ``body`` nearest its centroid (ties to the earlier hex)."""
    cx = sum(grid.px[i] for i in body) / len(body)
    cy = sum(grid.py[i] for i in body) / len(body)
    return min(body, key=lambda i: ((grid.px[i] - cx) ** 2 + (grid.py[i] - cy) ** 2, i))


def _drain_lakes(net: _RiverNet, hills: list[bool]) -> int:
    """Give every lake one outflow that drains off the map; return how many lakes
    had to be removed because none could be laid.

    Lakes are drained in Prim order: the undrained lake nearest drained water
    (the map edge, or a hex of a drained lake or river) goes next, routed toward
    that water and, failing that, toward the edge. Its course and its own hexes
    then count as drained water for the lakes after it.
    """
    grid = net.grid
    # Per undrained lake: distance from its centre to the nearest drained water,
    # and that water's hex (`_TO_EDGE` for the map edge).
    nearest = {
        k: (grid.edge_distance(center), _TO_EDGE)
        for k, center in enumerate(net.centers)
    }
    dropped = 0
    while nearest:
        k = min(nearest, key=lambda k: (nearest[k][0], k))
        _, toward = nearest.pop(k)
        course = _lake_outflow(net, k, toward, hills)
        if course is None:
            net.remove_lake(k)
            dropped += 1
            continue
        for other, (best, hex_) in nearest.items():
            for point in course[::2] + net.lakes[k][::2]:
                span = grid.distance(net.centers[other], point)
                if span < best:
                    best, hex_ = span, point
            nearest[other] = (best, hex_)
    return dropped


def _lake_outflow(
    net: _RiverNet, k: int, toward: int, hills: list[bool]
) -> list[int] | None:
    """Lay lake ``k``'s outflow, heading for hex ``toward`` (or the map edge) and
    then for the edge; the course laid, or ``None`` if neither attempt fits."""
    attempts = ((toward, LAKE_OUTFLOW_REACH), (_TO_EDGE, LAKE_OUTFLOW_FALLBACK_REACH))
    for goal, max_len in attempts:
        found = net.route(_shore_starts(net, k, hills), goal, max_len)
        if found is not None:
            course, joined = found
            if not net.touches_itself(course) and not net.walls_off_land(course):
                net.commit(course, k, joined)
                return course
        if goal == _TO_EDGE:
            break
    return None


def _shore_starts(net: _RiverNet, k: int, hills: list[bool]) -> list[tuple[int, int]]:
    """(hex, heading) starts for an outflow of lake ``k``: dry shore hexes off the
    map edge touching only this lake, heading away from it (straight out or a
    gentle turn)."""
    grid = net.grid
    starts: list[tuple[int, int]] = []
    seen: set[int] = set()
    for i in net.lakes[k]:
        for out, n in enumerate(grid.neighbor_in[i]):
            if n < 0 or net.water(n) or hills[n] or n in seen or grid.on_edge(n):
                continue
            if any(net.water(m) and net.system[m] != k for m in grid.neighbors[n]):
                continue
            seen.add(n)
            starts.extend((n, (out + t) % 6) for t in (-1, 0, 1))
    return starts


def _springs(
    net: _RiverNet, rng: random.Random, hills: list[bool], target: int
) -> list[int]:
    """Rivers from springs at the foot of hills into the nearest drained water or
    off the map edge, until the net holds ``target`` river hexes or springs run
    out; returns the springs used (each a course's first hex).

    A spring keeps `SPRING_EDGE_MARGIN` from the border, so a course end on the
    edge always means the river flows off the map.
    """
    grid = net.grid
    springs = [
        i
        for i in range(grid.size)
        if not hills[i]
        and not net.water(i)
        and grid.edge_distance(i) >= SPRING_EDGE_MARGIN
        and any(hills[n] for n in grid.neighbors[i])
    ]
    rng.shuffle(springs)
    # Sampled hexes of drained water, to aim each spring at the nearest.
    drained_points = [i for course in net.courses for i in course[::3]] + [
        center for k, center in enumerate(net.centers) if not net.dropped[k]
    ]
    used: list[int] = []
    for spring in springs:
        if net.river_hexes >= target:
            break
        if net.wet_neighbors[spring] or any(
            grid.distance(spring, u) < SPRING_SPACING for u in used
        ):
            continue
        if any(net.water(j) for j in grid.disc(spring, SPRING_DRY_RADIUS)):
            continue
        reach, goal = grid.edge_distance(spring), _TO_EDGE
        for point in drained_points:
            span = grid.distance(spring, point)
            if span < reach:
                reach, goal = span, point
        if reach > RIVER_MAX_LEN:
            continue
        starts = [
            (spring, d)
            for d, n in enumerate(grid.neighbor_in[spring])
            if n >= 0 and not hills[n]
        ]
        found = net.route(starts, goal, RIVER_MAX_LEN)
        if found is None:
            continue
        course, joined = found
        if (
            len(course) < SPRING_MIN_LEN
            or net.touches_itself(course)
            or net.walls_off_land(course)
        ):
            continue
        net.commit(course, net.new_system(), joined)
        used.append(spring)
        drained_points.extend(course[::3])
    return used


# --- Woods ------------------------------------------------------------------------


def _woods(
    grid: HexGrid,
    rng: random.Random,
    ruggedness: list[float],
    hills: list[bool],
    water: list[bool],
    share: float,
) -> list[bool]:
    """Woods up to ``share`` of the map, as separate blobs `WOOD_GAP` apart.

    No wood grows past its drawn size (`WOOD_KINDS`), so none is bigger than 30
    hexes; hexes `GLADE_DEPTH` deep in a forest are then opened.
    """
    weight = _wood_weights(grid, rng, ruggedness, hills, water)
    shape = fbm(grid, rng, feature=5.0, octaves=2)
    top = max(weight)
    pool = [i for i in range(grid.size) if weight[i] > 0]
    woods = [False] * grid.size
    near_woods = [False] * grid.size  # within WOOD_GAP of a wood

    def may_take(i: int) -> bool:
        return weight[i] > 0 and not near_woods[i]

    target = round(share * grid.size)
    placed = misses = 0
    while placed < target and misses < WOOD_MAX_MISSES:
        seed = _weighted_pick(rng, pool, weight, top)
        if not may_take(seed):
            misses += 1
            continue

        def priority(i: int, seed: int = seed) -> float:
            return shape[i] + 0.02 * grid.distance(i, seed)

        wood = _grow_blob(grid, seed, _draw_size(rng, WOOD_KINDS), priority, may_take)
        for i in wood:
            woods[i] = True
            for j in grid.disc(i, WOOD_GAP):
                near_woods[j] = True
        placed += len(wood)
    for i, d in enumerate(depth(grid, woods)):
        if d >= GLADE_DEPTH:
            woods[i] = False
    return woods


def _wood_weights(
    grid: HexGrid,
    rng: random.Random,
    ruggedness: list[float],
    hills: list[bool],
    water: list[bool],
) -> list[float]:
    """Seed weight per hex for woods (0 on hills and water).

    A steep regional field makes some regions close country and others open;
    hexes beside hills, and banks in some stretches, get a bonus.
    """
    region = rank_normalise(fbm(grid, rng, feature=85.0, octaves=2))
    banks = rank_normalise(fbm(grid, rng, feature=30.0, octaves=2))
    weight = [0.0] * grid.size
    for i in range(grid.size):
        if hills[i] or water[i]:
            continue
        w = 0.03 + region[i] ** 3.5 + 0.12 * ruggedness[i] ** 2
        if any(hills[n] for n in grid.neighbors[i]):
            w += 0.3
        if banks[i] > 0.6 and any(water[n] for n in grid.neighbors[i]):
            w += 0.3
        weight[i] = w
    return weight
