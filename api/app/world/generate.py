"""World generator (WORLD_SPEC §6): seeded, deterministic, pure.

``generate(params)`` builds a whole world map for ``params.slots`` start regions:

1. **Terrain:** plains with fBm-noise woods (~15%) and hills (~10%).
2. **Rivers:** meandering impassable hex chains walked inland from the map edge,
   plus tributaries joining existing rivers.
3. **Settlements:** about one per ``hexes_per_settlement`` hexes, spread by
   best-candidate sampling; ~15% are cities.
4. **Capitals:** one settlement per slot, spread by farthest-point sampling and
   relaxed with Lloyd steps, then promoted to city.
5. **Roads:** a spanning tree over each settlement's nearest neighbours plus ~30%
   redundant short links, each laid by least-cost search that favours reusing
   road (so roads merge into networks). A road over a river is a bridge.
6. **Fix-ups (fairness):** each start region (*territory*, the land nearest its
   capital) gets one connected road network, a road across every real border,
   and at least half the median settlement count.
7. **Provinces:** every settlement anchors the hexes nearest it by column travel
   cost.

Same params → same world, on any machine: the only randomness is one
`random.Random` seeded from a string (hash-independent). The generator ports the
sizing study's PoC (VISION §12) without numpy so it adds no runtime deps; a
200-slot world takes tens of seconds.

`GeneratedWorld.to_state` turns a world into a turn-0 `GameState` with some slots
filled; the rest stay neutral land for relocation and new players (VISION §7).
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.engine.hex_coords import Axial, distance, within
from app.engine.models import (
    GameConfig,
    GameMap,
    GameState,
    Hex,
    Player,
    PlayerId,
    Settlement,
    Unit,
    VictoryConfig,
    default_facing,
)
from app.engine.stats import COLUMN_RULES, Terrain, UnitType
from app.world.grid import (
    HexGrid,
    astar,
    fbm,
    multi_source_bfs,
    multi_source_dijkstra,
)

HEXES_PER_SLOT = 610
"""Map area per slot at the default sizing (VISION §1, §12: 200 slots ≈ 350²)."""

STARTING_UNITS: tuple[UnitType, ...] = (
    UnitType.INFANTRY,
    UnitType.INFANTRY,
    UnitType.ARMOR,
    UnitType.ARTILLERY,
)
"""Each filled slot's opening force (WORLD_SPEC §6), garrison first."""

WORLD_UNIT_CAP = 15
"""Command cap ceiling (WORLD_SPEC §7). The holdings-based cap comes with W4."""

# Road laying: entering an existing road hex is cheap, so roads merge; crossing a
# river (a bridge) is expensive, so bridges are few.
_ROAD_REUSE_COST = 0.6
_ROAD_TERRAIN_COST: dict[Terrain, float] = {
    Terrain.PLAINS: 1.0,
    Terrain.WOODS: 2.0,
    Terrain.HILLS: 2.0,
    Terrain.RIVER: 6.0,
}
_ROAD_NEIGHBORS = 6  # nearest settlements considered for the road graph
_ROAD_EXTRA_SHARE = 0.3  # redundant links, as a share of spanning-tree links
_BORDER_MIN_CONTACTS = 4  # territory pairs touching less than this need no road
_FIXUP_ROUNDS = 8
_LLOYD_STEPS = 6


class WorldGenError(Exception):
    """World parameters are unusable, or the fairness fix-ups could not converge."""


@dataclass(frozen=True)
class WorldParams:
    """Inputs to `generate`. ``for_slots`` sizes the map from a slot count.

    Raises `WorldGenError` on construction for unusable values (this is the
    generator's boundary: parameters come from a CLI or an admin request).
    """

    width: int
    height: int
    slots: int
    seed: int
    hexes_per_settlement: int = 70
    city_share: float = 0.15
    woods_share: float = 0.15
    hills_share: float = 0.10
    hexes_per_river: int = 2500

    def __post_init__(self) -> None:
        if self.width < 12 or self.height < 12:
            raise WorldGenError(
                f"world must be at least 12×12, got {self.width}×{self.height}"
            )
        if self.slots < 1:
            raise WorldGenError(f"a world needs at least one slot, got {self.slots}")
        settlements = self.width * self.height // self.hexes_per_settlement
        if settlements < 2 * self.slots:
            raise WorldGenError(
                f"{self.width}×{self.height} at one settlement per "
                f"{self.hexes_per_settlement} hexes gives {settlements} settlements, "
                f"too few for {self.slots} slots"
            )
        for name in ("city_share", "woods_share", "hills_share"):
            share = getattr(self, name)
            if not 0.0 <= share < 1.0:
                raise WorldGenError(f"{name} must be in [0, 1), got {share}")

    @classmethod
    def for_slots(cls, slots: int, seed: int) -> WorldParams:
        """A square world with `HEXES_PER_SLOT` hexes per slot."""
        side = round(math.sqrt(HEXES_PER_SLOT * slots))
        return cls(width=side, height=side, slots=slots, seed=seed)


@dataclass(frozen=True)
class GeneratedWorld:
    """A generated world: the static map plus the layout facts built with it.

    Per-hex lists (``territory``, ``province``) align with ``game_map.hexes``.
    ``territory`` is the slot whose start region holds the hex, ``province`` the
    position in ``settlements`` of the settlement anchoring it; ``-1`` marks
    impassable or unreachable hexes. ``fill_order`` lists slots so that any prefix
    is spread across the map: fill the first N for N players.
    """

    params: WorldParams
    game_map: GameMap
    settlements: list[Axial]
    capitals: list[Axial]
    fill_order: list[int]
    territory: list[int]
    province: list[int]
    stats: dict[str, float] = field(default_factory=dict)

    def to_state(self, players: list[PlayerId]) -> GameState:
        """A turn-0 state with ``players`` filling the first slots of ``fill_order``.

        Each player controls their capital and gets `STARTING_UNITS` on and
        around it; every other settlement starts neutral. Raises `WorldGenError`
        if there are more players than slots or a player id repeats.
        """
        if len(players) > self.params.slots:
            raise WorldGenError(
                f"{len(players)} players for a {self.params.slots}-slot world"
            )
        if len(set(players)) != len(players):
            raise WorldGenError("player ids must be unique")
        capital_owner = {
            self.capitals[self.fill_order[k]]: player
            for k, player in enumerate(players)
        }
        settlements = [
            Settlement(coord=coord, controlled_by=capital_owner.get(coord))
            for coord in self.settlements
        ]
        units = self._starting_units(players)
        for unit in units:
            unit.facing = default_facing(unit.coord, unit.owner, units)
        return GameState(
            turn=0,
            map=self.game_map,
            settlements=settlements,
            units=units,
            players=[Player(id=p) for p in players],
            config=GameConfig(
                unit_cap=WORLD_UNIT_CAP,
                victory=VictoryConfig(turn_limit=1_000_000, city_elimination=False),
            ),
        )

    def _starting_units(self, players: list[PlayerId]) -> list[Unit]:
        """Place each player's opening force: the garrison on the capital, the
        rest on the nearest free open hexes (by distance, then axial order)."""
        index = self.game_map.index
        settlement_hexes = set(self.settlements)
        taken: set[Axial] = set()
        units: list[Unit] = []
        for k, player in enumerate(players):
            capital = self.capitals[self.fill_order[k]]
            spots = [capital] + _nearest_open(capital, index, settlement_hexes, taken)
            for unit_type, coord in zip(STARTING_UNITS, spots, strict=False):
                taken.add(coord)
                units.append(
                    Unit(
                        id=len(units) + 1,
                        type=unit_type,
                        owner=player,
                        coord=coord,
                        hp=10,
                    )
                )
        return units


def _nearest_open(
    center: Axial,
    index: dict[Axial, Hex],
    settlement_hexes: set[Axial],
    taken: set[Axial],
) -> list[Axial]:
    """Free plains/woods/hills hexes near ``center``, nearest first (up to radius 3)."""
    open_terrain = {Terrain.PLAINS, Terrain.WOODS, Terrain.HILLS}
    spots = [
        c
        for c in within(center, 3)
        if c in index
        and index[c].terrain in open_terrain
        and c not in settlement_hexes
        and c not in taken
    ]
    spots.sort(key=lambda c: (distance(center, c), c.q, c.r))
    if len(spots) < len(STARTING_UNITS) - 1:
        raise WorldGenError(f"no room for a starting force around {center}")
    return spots


# --- Pipeline -------------------------------------------------------------------


@dataclass
class _Draft:
    """Mutable working state while a world is generated."""

    grid: HexGrid
    rng: random.Random
    terrain: list[Terrain]
    river: list[bool]
    road: list[bool] = field(default_factory=list)
    settlements: list[int] = field(default_factory=list)
    cities: list[bool] = field(default_factory=list)
    capitals: list[int] = field(default_factory=list)  # hex index per slot
    territory: list[int] = field(default_factory=list)

    def passable(self, i: int) -> bool:
        return not self.river[i] or self.road[i]

    def lay_road(
        self, start: int, goal: int, allowed: Callable[[int], bool] | None = None
    ) -> None:
        """Lay a least-cost road from ``start`` to ``goal``, reusing road cheaply."""

        def cost(v: int) -> float:
            return (
                _ROAD_REUSE_COST
                if self.road[v]
                else _ROAD_TERRAIN_COST[self.terrain[v]]
            )

        path = astar(self.grid, start, goal, cost, _ROAD_REUSE_COST, allowed)
        if path is None:
            raise WorldGenError(f"no road possible from hex {start} to hex {goal}")
        for i in path:
            self.road[i] = True


def generate(params: WorldParams) -> GeneratedWorld:
    """Generate the world for ``params`` (pure; deterministic in ``params``)."""
    grid = HexGrid(params.width, params.height)
    rng = random.Random(
        f"hex-general-world:{params.seed}:{params.width}x{params.height}:{params.slots}"
    )
    draft = _Draft(
        grid=grid, rng=rng, terrain=_base_terrain(grid, rng, params), river=[]
    )
    draft.river = _carve_rivers(grid, rng, params)
    for i, is_river in enumerate(draft.river):
        if is_river:
            draft.terrain[i] = Terrain.RIVER
    draft.road = [False] * grid.size

    count = grid.size // params.hexes_per_settlement
    draft.settlements = _place_settlements(grid, rng, draft.river, count)
    draft.cities = [rng.random() < params.city_share for _ in draft.settlements]
    capital_picks = _choose_capitals(draft, params.slots)
    for k in capital_picks:
        draft.cities[k] = True
    draft.capitals = [draft.settlements[k] for k in capital_picks]

    for a, b in _road_plan(draft):
        draft.lay_road(draft.settlements[a], draft.settlements[b])
    _fix_up(draft)
    return _finish(draft, params)


def _base_terrain(
    grid: HexGrid, rng: random.Random, params: WorldParams
) -> list[Terrain]:
    """Plains with noise-shaped woods and hills at the requested shares."""
    terrain = [Terrain.PLAINS] * grid.size
    woods_noise = fbm(grid, rng, feature=16.0)
    hills_noise = fbm(grid, rng, feature=22.0)
    woods_cut = sorted(woods_noise)[
        min(grid.size - 1, int((1 - params.woods_share) * grid.size))
    ]
    woods = [v >= woods_cut for v in woods_noise]
    rest = sorted(
        (i for i in range(grid.size) if not woods[i]), key=lambda i: -hills_noise[i]
    )
    for i in rest[: round(params.hills_share * grid.size)]:
        terrain[i] = Terrain.HILLS
    for i in range(grid.size):
        if woods[i]:
            terrain[i] = Terrain.WOODS
    return terrain


def _carve_rivers(grid: HexGrid, rng: random.Random, params: WorldParams) -> list[bool]:
    """Walk rivers inland from the map edge, and tributaries off existing rivers."""
    river = [False] * grid.size
    river_hexes: list[int] = []
    edge = [i for i in range(grid.size) if grid.on_edge(i)]
    cx = sum(grid.px) / grid.size
    cy = sum(grid.py) / grid.size
    placed = 0
    for _ in range(max(1, round(grid.size / params.hexes_per_river))):
        best: list[int] = []
        for _attempt in range(40):
            length = rng.randint(30, 120)
            tributary = placed > 0 and rng.random() < 0.35
            if tributary:
                mouth = rng.choice(river_hexes)
                headings = [
                    k
                    for k, n in enumerate(grid.neighbor_in[mouth])
                    if n >= 0 and not river[n]
                ]
                if not headings:
                    continue
                heading = rng.choice(headings)
                start = grid.neighbor_in[mouth][heading]
            else:
                start = rng.choice(edge)
                if river[start] or any(river[w] for w in grid.neighbors[start]):
                    continue
                vx, vy = cx - grid.px[start], cy - grid.py[start]
                heading = max(
                    range(6),
                    key=lambda k: grid.direction_px[k][0] * vx
                    + grid.direction_px[k][1] * vy,
                )
            path = _river_walk(grid, rng, river, start, heading, length, tributary)
            if len(path) > len(best):
                best = path
            if len(path) >= 30:
                break
        if best:
            for i in best:
                river[i] = True
            river_hexes.extend(best)
            placed += 1
    return river


def _river_walk(
    grid: HexGrid,
    rng: random.Random,
    river: list[bool],
    start: int,
    heading: int,
    length: int,
    tributary: bool,
) -> list[int]:
    """Meander upstream from ``start``, staying within ±1 of ``heading``.

    Stops at the map edge, before touching another river or itself, or at
    ``length`` hexes. A tributary may touch its parent river at its mouth.
    """
    path = [start]
    body = {start}
    direction = heading
    while len(path) < length:
        current = path[-1]
        roll = rng.random()
        if roll < 0.5:
            first = direction
        elif roll < 0.75:
            first = (direction + 1) % 6
        else:
            first = (direction - 1) % 6
        order = [first] + [
            d
            for d in (direction, (direction + 1) % 6, (direction - 1) % 6)
            if d != first
        ]
        moved = False
        reached_edge = False
        for d in order:
            if (d - heading) % 6 not in (0, 1, 5):
                continue
            nxt = grid.neighbor_in[current][d]
            if nxt < 0:
                reached_edge = True
                break
            if river[nxt] or nxt in body:
                continue
            around = grid.neighbors[nxt]
            if any(river[w] for w in around) and not (tributary and len(path) <= 1):
                continue
            if any(w in body and w != current for w in around):
                continue
            path.append(nxt)
            body.add(nxt)
            direction = d
            moved = True
            break
        if reached_edge or not moved:
            break
    return path


def _place_settlements(
    grid: HexGrid, rng: random.Random, river: list[bool], count: int
) -> list[int]:
    """Best-candidate (Mitchell) sampling: of 12 random open hexes, take the one
    farthest from every settlement so far. Map-edge hexes are avoided."""
    spacing = math.sqrt(grid.size / count)
    radius = int(2 * spacing) + 2
    nearest = [math.inf] * grid.size
    pool = [i for i in range(grid.size) if not river[i] and not grid.on_edge(i)]
    taken = [False] * grid.size
    chosen: list[int] = []
    for _ in range(count):
        candidates: list[int] = []
        while not candidates:
            candidates = [
                c for c in (rng.choice(pool) for _ in range(12)) if not taken[c]
            ]
        best = max(candidates, key=lambda c: nearest[c])
        chosen.append(best)
        taken[best] = True
        bq, br = grid.q[best], grid.r[best]
        for i in grid.window(best, radius):
            dq = grid.q[i] - bq
            dr = grid.r[i] - br
            d = (abs(dq) + abs(dr) + abs(dq + dr)) // 2
            if d < nearest[i]:
                nearest[i] = d
    return chosen


def _choose_capitals(draft: _Draft, slots: int) -> list[int]:
    """Pick one settlement per slot (positions in ``draft.settlements``).

    Farthest-point sampling (cities slightly preferred) spreads the picks; four
    Lloyd steps then move each to the settlement nearest its region's centroid.
    """
    grid = draft.grid
    sites = draft.settlements
    if len(sites) < slots:
        raise WorldGenError(f"{len(sites)} settlements cannot seat {slots} slots")
    xs = [grid.px[s] for s in sites]
    ys = [grid.py[s] for s in sites]
    spacing = math.sqrt(max(grid.px) * max(grid.py) / slots)
    bonus = [0.15 * spacing if city else 0.0 for city in draft.cities]
    cx = sum(grid.px) / grid.size
    cy = sum(grid.py) / grid.size
    first = min(
        range(len(sites)), key=lambda k: math.hypot(xs[k] - cx, ys[k] - cy) - bonus[k]
    )
    chosen = [first]
    nearest = [
        math.hypot(xs[k] - xs[first], ys[k] - ys[first]) for k in range(len(sites))
    ]
    picked = {first}
    for _ in range(slots - 1):
        j = max(
            (k for k in range(len(sites)) if k not in picked),
            key=lambda k: nearest[k] + bonus[k],
        )
        chosen.append(j)
        picked.add(j)
        for k in range(len(sites)):
            d = math.hypot(xs[k] - xs[j], ys[k] - ys[j])
            if d < nearest[k]:
                nearest[k] = d

    open_hex = [not r for r in draft.river]
    points = [sites[k] for k in chosen]
    for _ in range(_LLOYD_STEPS):
        points = _lloyd_step(grid, points, open_hex)
    return _snap_capitals(draft, points, open_hex)


def _lloyd_step(grid: HexGrid, points: list[int], open_hex: list[bool]) -> list[int]:
    """Move each point to the open hex nearest its region's centroid."""
    owner, _ = multi_source_bfs(grid, points, open_hex)
    count = len(points)
    sum_x = [0.0] * count
    sum_y = [0.0] * count
    size = [0] * count
    for i, p in enumerate(owner):
        if p >= 0:
            sum_x[p] += grid.px[i]
            sum_y[p] += grid.py[i]
            size[p] += 1
    best = list(points)
    best_d = [math.inf] * count
    for i, p in enumerate(owner):
        if p < 0:
            continue
        d = math.hypot(grid.px[i] - sum_x[p] / size[p], grid.py[i] - sum_y[p] / size[p])
        if d < best_d[p]:
            best_d[p] = d
            best[p] = i
    return best


def _snap_capitals(draft: _Draft, points: list[int], open_hex: list[bool]) -> list[int]:
    """Seat each relaxed point on a nearby settlement, keeping capitals apart.

    Each region takes its settlement nearest the point (cities preferred) that is
    at least 60% of the ideal lattice spacing from capitals already seated; if no
    settlement qualifies, the nearest one anywhere still unseated.
    """
    grid = draft.grid
    sites = draft.settlements
    owner, _ = multi_source_bfs(grid, points, open_hex)
    area = sum(open_hex) / len(points)
    min_spacing = int(0.6 * math.sqrt(area / 0.75))
    seated: list[int] = []
    used: set[int] = set()
    for p, point in enumerate(points):

        def score(k: int, point: int = point) -> tuple[float, int]:
            d = math.hypot(
                grid.px[sites[k]] - grid.px[point], grid.py[sites[k]] - grid.py[point]
            )
            return (d * (0.85 if draft.cities[k] else 1.0), k)

        free = [k for k in range(len(sites)) if k not in used]
        spaced = [
            k
            for k in free
            if owner[sites[k]] == p
            and all(grid.distance(sites[k], sites[c]) >= min_spacing for c in seated)
        ]
        pick = min(spaced or free, key=score)
        seated.append(pick)
        used.add(pick)
    return seated


def _road_plan(draft: _Draft) -> list[tuple[int, int]]:
    """Settlement pairs to connect, in laying order.

    A minimum spanning tree over each settlement's nearest neighbours (so every
    settlement is road-reachable) followed by redundant short links (so no single
    bridge decides a region).
    """
    grid = draft.grid
    sites = draft.settlements
    n = len(sites)
    bucket = max(4, int(2 * math.sqrt(grid.size / n)))
    buckets: dict[tuple[int, int], list[int]] = {}
    for k, s in enumerate(sites):
        buckets.setdefault((grid.col[s] // bucket, grid.row[s] // bucket), []).append(k)

    weights: dict[tuple[int, int], int] = {}
    for k, s in enumerate(sites):
        bc, br = grid.col[s] // bucket, grid.row[s] // bucket
        near = [
            j
            for dc in (-2, -1, 0, 1, 2)
            for dr in (-2, -1, 0, 1, 2)
            for j in buckets.get((bc + dc, br + dr), [])
            if j != k
        ]
        near.sort(key=lambda j: (grid.distance(s, sites[j]), j))
        for j in near[:_ROAD_NEIGHBORS]:
            weights[(min(k, j), max(k, j))] = grid.distance(s, sites[j])

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in weights:
        parent[find(a)] = find(b)
    while len({find(k) for k in range(n)}) > 1:
        # Rare: a cluster with no near neighbours outside it. Join it to the
        # nearest settlement of the main cluster.
        root = find(0)
        inside = [k for k in range(n) if find(k) == root]
        outside = [k for k in range(n) if find(k) != root]
        a, b = min(
            ((i, o) for i in inside for o in outside),
            key=lambda e: (grid.distance(sites[e[0]], sites[e[1]]), e),
        )
        weights[(min(a, b), max(a, b))] = grid.distance(sites[a], sites[b])
        parent[find(a)] = find(b)

    parent = list(range(n))
    ordered = sorted(weights.items(), key=lambda e: (e[1], e[0]))
    tree: list[tuple[int, int]] = []
    spare: list[tuple[int, int]] = []
    for pair, _ in ordered:
        ra, rb = find(pair[0]), find(pair[1])
        if ra == rb:
            spare.append(pair)
        else:
            parent[ra] = rb
            tree.append(pair)
    short = spare[: max(1, int(0.6 * len(spare)))] if spare else []
    extra_count = min(len(short), round(_ROAD_EXTRA_SHARE * len(tree)))
    extra = sorted(draft.rng.sample(short, extra_count))
    return tree + extra


# --- Fairness fix-ups -------------------------------------------------------------


def _fix_up(draft: _Draft) -> None:
    """Repeat the fairness fix-ups until none changes anything.

    Territories are each capital's nearest land in steps over passable hexes, so
    they do not depend on roads; the fix-ups lay roads only inside territories
    and so never move a border.
    """
    for _ in range(_FIXUP_ROUNDS):
        passable = [draft.passable(i) for i in range(draft.grid.size)]
        draft.territory, _ = multi_source_bfs(draft.grid, draft.capitals, passable)
        changed = _road_every_border(draft)
        changed = _join_territory_roads(draft) or changed
        changed = _settle_sparse_territories(draft) or changed
        if not changed:
            return
    raise WorldGenError(f"fairness fix-ups did not settle in {_FIXUP_ROUNDS} rounds")


def _road_every_border(draft: _Draft) -> bool:
    """Lay a capital-to-capital road across any real border no road crosses."""
    grid, territory, road = draft.grid, draft.territory, draft.road
    contacts: Counter[tuple[int, int]] = Counter()
    crossed: set[tuple[int, int]] = set()
    for i in range(grid.size):
        p = territory[i]
        if p < 0:
            continue
        for v in grid.neighbors[i]:
            q = territory[v]
            if q > p:
                contacts[(p, q)] += 1
                if road[i] and road[v]:
                    crossed.add((p, q))
    changed = False
    for pair in sorted(contacts):
        if contacts[pair] < _BORDER_MIN_CONTACTS or pair in crossed:
            continue
        p, q = pair
        draft.lay_road(
            draft.capitals[p],
            draft.capitals[q],
            allowed=_inside(territory, p, q),
        )
        changed = True
    return changed


def _join_territory_roads(draft: _Draft) -> bool:
    """Connect every road fragment in a territory to its capital's network."""
    changed = False
    for p, capital in enumerate(draft.capitals):
        fragments = _road_fragments(draft, p)
        for fragment in fragments:
            if capital in fragment:
                continue
            draft.lay_road(
                min(fragment),
                capital,
                allowed=_inside(draft.territory, p),
            )
            changed = True
    return changed


def _inside(territory: list[int], *slots: int) -> Callable[[int], bool]:
    """A predicate: is a hex inside the territories of ``slots``?"""
    wanted = frozenset(slots)
    return lambda i: territory[i] in wanted


def _road_fragments(draft: _Draft, slot: int) -> list[set[int]]:
    """Connected groups of road hexes inside ``slot``'s territory."""
    grid, territory, road = draft.grid, draft.territory, draft.road
    seen: set[int] = set()
    fragments: list[set[int]] = []
    for i in range(grid.size):
        if not road[i] or territory[i] != slot or i in seen:
            continue
        group = {i}
        queue = deque([i])
        while queue:
            u = queue.popleft()
            for v in grid.neighbors[u]:
                if road[v] and territory[v] == slot and v not in group:
                    group.add(v)
                    queue.append(v)
        seen |= group
        fragments.append(group)
    return fragments


def _settle_sparse_territories(draft: _Draft) -> bool:
    """Give any territory short of half the median settlement count a new town.

    The town goes on the open hex farthest from every settlement, and is roaded
    to the capital inside the territory. One town per territory per round.
    """
    grid = draft.grid
    counts = _settlements_per_territory(draft)
    floor = max(1, int(0.5 * statistics.median(counts)))
    lacking = [p for p, c in enumerate(counts) if c < floor]
    if not lacking:
        return False
    open_hex = [not r for r in draft.river]
    _, spacing = multi_source_bfs(grid, draft.settlements, open_hex)
    occupied = set(draft.settlements)
    for p in lacking:
        sites = [
            i
            for i in range(grid.size)
            if draft.territory[i] == p
            and not draft.river[i]
            and not grid.on_edge(i)
            and i not in occupied
        ]
        if not sites:
            raise WorldGenError(f"territory {p} has no room for another settlement")
        site = max(sites, key=lambda i: (spacing[i], -i))
        draft.settlements.append(site)
        draft.cities.append(False)
        occupied.add(site)
        draft.lay_road(
            site,
            draft.capitals[p],
            allowed=_inside(draft.territory, p),
        )
    return True


def _settlements_per_territory(draft: _Draft) -> list[int]:
    """Settlements in each territory, not counting its capital."""
    counts = [0] * len(draft.capitals)
    capitals = set(draft.capitals)
    for s in draft.settlements:
        p = draft.territory[s]
        if p >= 0 and s not in capitals:
            counts[p] += 1
    return counts


# --- Assembly ---------------------------------------------------------------------


def _finish(draft: _Draft, params: WorldParams) -> GeneratedWorld:
    """Freeze the draft into a `GeneratedWorld` with provinces and stats."""
    grid = draft.grid
    terrain = list(draft.terrain)
    for i in range(grid.size):
        if draft.river[i] and draft.road[i]:
            terrain[i] = Terrain.BRIDGE
    for s, city in zip(draft.settlements, draft.cities, strict=True):
        terrain[s] = Terrain.CITY if city else Terrain.TOWN

    entry_cost = [
        (
            0
            if not draft.passable(i)
            else (
                COLUMN_RULES.road_step_pulses
                if draft.road[i]
                else COLUMN_RULES.offroad_step_pulses
            )
        )
        for i in range(grid.size)
    ]
    province = multi_source_dijkstra(grid, draft.settlements, entry_cost)

    coords = [Axial(q=grid.q[i], r=grid.r[i]) for i in range(grid.size)]
    game_map = GameMap(
        width=grid.width,
        height=grid.height,
        hexes=[
            Hex(coord=coords[i], terrain=terrain[i], road=draft.road[i])
            for i in range(grid.size)
        ],
    )
    return GeneratedWorld(
        params=params,
        game_map=game_map,
        settlements=[coords[s] for s in draft.settlements],
        capitals=[coords[c] for c in draft.capitals],
        fill_order=_fill_order(grid, draft.capitals),
        territory=list(draft.territory),
        province=province,
        stats=_stats(draft, terrain),
    )


def _fill_order(grid: HexGrid, capitals: list[int]) -> list[int]:
    """Slots ordered so every prefix is spread out: start nearest the map centre,
    then repeatedly take the capital farthest from those already taken."""
    cx = sum(grid.px) / grid.size
    cy = sum(grid.py) / grid.size
    first = min(
        range(len(capitals)),
        key=lambda p: (
            math.hypot(grid.px[capitals[p]] - cx, grid.py[capitals[p]] - cy),
            p,
        ),
    )
    order = [first]
    nearest = [grid.distance(capitals[first], c) for c in capitals]
    remaining = set(range(len(capitals))) - {first}
    while remaining:
        nxt = max(remaining, key=lambda p: (nearest[p], -p))
        order.append(nxt)
        remaining.discard(nxt)
        for p in remaining:
            nearest[p] = min(nearest[p], grid.distance(capitals[nxt], capitals[p]))
    return order


def _stats(draft: _Draft, terrain: list[Terrain]) -> dict[str, float]:
    """Summary numbers for review and the fairness gates."""
    grid = draft.grid
    shares = Counter(terrain)
    per_territory = _settlements_per_territory(draft)
    territory_size = Counter(p for p in draft.territory if p >= 0)
    spacing = min(
        (
            grid.distance(a, b)
            for k, a in enumerate(draft.capitals)
            for b in draft.capitals[k + 1 :]
        ),
        default=0,
    )
    return {
        "hexes": grid.size,
        "share_woods": round(shares[Terrain.WOODS] / grid.size, 4),
        "share_hills": round(shares[Terrain.HILLS] / grid.size, 4),
        "share_river": round(shares[Terrain.RIVER] / grid.size, 4),
        "bridges": shares[Terrain.BRIDGE],
        "share_road": round(sum(draft.road) / grid.size, 4),
        "settlements": len(draft.settlements),
        "cities": sum(draft.cities),
        "slots": len(draft.capitals),
        "min_capital_distance": spacing,
        "territory_hexes_min": min(territory_size.values()),
        "territory_hexes_max": max(territory_size.values()),
        "territory_settlements_min": min(per_territory),
        "territory_settlements_median": statistics.median(per_territory),
        "territory_settlements_max": max(per_territory),
        "unclaimed_passable_hexes": sum(
            1 for i in range(grid.size) if draft.passable(i) and draft.territory[i] < 0
        ),
    }
