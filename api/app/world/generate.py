"""World generator (WORLD_SPEC §6): seeded, deterministic, pure.

``generate(params)`` builds a whole world map for ``params.slots`` start regions:

1. **Land** (`app.world.land`): hills as ridges along regional highland belts,
   lakes of 3–22 hexes, rivers that rise at lakes or springs by hills and always
   drain off the map edge, and woods from copses to 30-hex forests.
2. **Settlements:** about one per ``hexes_per_settlement`` hexes, spread by
   best-candidate sampling weighted toward good town ground (open, waterside,
   lowland). Cities (``city_share`` of them) are picked by farthest-point
   sampling, so they are spread-out hubs.
3. **Seats:** one settlement per slot, spread by farthest-point sampling and
   relaxed with Lloyd steps. A seat is a town: its slot's base goes beside it.
4. **Roads:** a spanning tree over each settlement's nearest neighbours plus ~30%
   redundant short links, each laid by least-cost search that prices terrain,
   reuses road cheaply (so roads merge), wiggles with a noise field, shuns
   running beside another road and never makes a sharp turn on new ground. A
   road crosses a river only where the river runs plainly (a bridge); it never
   touches a lake.
5. **Fix-ups (fairness):** in each start region (*territory*, the land nearest
   its seat) every settlement reaches the seat by road inside the territory,
   every real border is crossed by a road, and each has at least half the median
   settlement count.
6. **Base plots:** each slot's base stands on open ground near its seat, with
   the four hexes of `BASE_FOOTPRINT` kept clear (user, 2026-11-10).
7. **Sites** (the ownership-bearing things, VISION §6): every city is a city
   site; every other town but a seat gets a farm, oil field, steel mill or
   factory on its own plains hex beside the town or a road, with a level drawn
   by `SITE_LEVEL_WEIGHTS`. Towns themselves hold no ownership.
8. **Provinces:** every site anchors the hexes nearest it by column travel cost.

Woods or hills under a base plot or a site become plains. Same params → same
world, on any machine: the only randomness is one `random.Random` seeded from a
string (hash-independent), and no loop runs over a set or dict whose order could
vary. Pure Python without numpy, so it adds no runtime deps; a 200-slot world
takes some seconds.

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

from app.engine.economy_rules import GARRISON_FORCES, SiteKind, WorldEconomy
from app.engine.hex_coords import Axial, distance, within
from app.engine.models import (
    BASE_FOOTPRINT,
    MAX_SITE_LEVEL,
    NEUTRAL,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    MapSite,
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
    patches,
    rank_normalise,
)
from app.world.land import Land, build_land

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
"""Hard unit ceiling in a world; the army size rule (`WorldEconomy.army`) binds first."""
WORLD_SETTLEMENT_REPAIR = 3
"""HP per turn regained resting on settlement ground (WORLD_SPEC §7)."""

SITE_LEVEL_WEIGHTS: tuple[tuple[int, float], ...] = ((1, 0.6), (2, 0.3), (3, 0.1))
"""How often a farm, oil field, steel mill or factory is generated at each level:
higher levels are rarer (provisional tuning, 2026-11-10)."""


# Site kinds (economy draft §03): per territory, one factory at 11–20 hexes, one
# oil field and one steel mill within 12, and in territories with at least six
# towns one more of either farther out. The other towns are farms.
_FACTORY_BAND = (11, 20)
_FACTORY_IDEAL = 14
_NEAR_RESOURCE = 12
_SECOND_RESOURCE_TOWNS = 6
# Garrison tier by distance to the nearest slot base: militia, garrison, strongpoint.
_TIER_NEAR = 10
_TIER_MID = 14
# Placement radii, each widened once before the generator gives up: a site within
# 2 (then 3) hexes of its town, a base footprint within 3 (then 5) of its seat.
_SITE_RADII = (2, 3)
_BASE_RADII = (3, 5)

ROAD_REUSE = 0.4
"""Cost of a road step onto existing road, so roads merge into networks."""
ROAD_COST: dict[Terrain, float] = {
    Terrain.PLAINS: 1.0,
    Terrain.WOODS: 2.2,
    Terrain.HILLS: 3.2,
    Terrain.RIVER: 7.0,
}
"""Cost of a road step onto new ground by terrain; a river step is a bridge."""
ROAD_PARALLEL = 0.8
"""Extra cost of a new road hex beside an existing road, against double roads."""
ROAD_WIGGLE = 0.6
"""Spread of the noise factor on off-road step costs, so roads are not ruler lines."""

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

    The shares are targets for the map area under woods, hills and river courses;
    ``hexes_per_settlement`` and ``hexes_per_lake`` set those densities. Raises `WorldGenError` on construction for unusable values (this is the
    generator's boundary: parameters come from a CLI or an admin request).
    """

    width: int
    height: int
    slots: int
    seed: int
    hexes_per_settlement: int = 70
    city_share: float = 0.15
    woods_share: float = 0.145
    hills_share: float = 0.07
    river_share: float = 0.024
    hexes_per_lake: int = 1000

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
        for name in ("city_share", "woods_share", "hills_share", "river_share"):
            share = getattr(self, name)
            if not 0.0 <= share < 1.0:
                raise WorldGenError(f"{name} must be in [0, 1), got {share}")
        if self.hexes_per_lake < 1:
            raise WorldGenError(
                f"hexes_per_lake must be at least 1, got {self.hexes_per_lake}"
            )

    @classmethod
    def for_slots(cls, slots: int, seed: int) -> WorldParams:
        """A square world with `HEXES_PER_SLOT` hexes per slot."""
        side = round(math.sqrt(HEXES_PER_SLOT * slots))
        return cls(width=side, height=side, slots=slots, seed=seed)


@dataclass(frozen=True)
class GeneratedWorld:
    """A generated world: the static map plus the layout facts built with it.

    ``settlements`` are the town and city hexes (terrain; ownership lives on
    ``game_map.sites``). Per slot, ``capitals`` is the base's anchor hex and
    ``seats`` the town it stands beside. Per-hex lists (``territory``,
    ``province``) align with ``game_map.hexes``. ``territory`` is the slot whose
    start region holds the hex, ``province`` the position in ``game_map.sites`` of
    the site anchoring it; ``-1`` marks impassable or unreachable hexes.
    ``fill_order`` lists slots so that any prefix is spread across the map: fill
    the first N for N players.
    """

    params: WorldParams
    game_map: GameMap
    settlements: list[Axial]
    capitals: list[Axial]
    seats: list[Axial]
    fill_order: list[int]
    territory: list[int]
    province: list[int]
    stats: dict[str, float] = field(default_factory=dict)

    @property
    def slots(self) -> list[Axial]:
        """Capitals (base anchors) in fill order: ``slots[k]`` is the capital of
        fill position k."""
        return [self.capitals[k] for k in self.fill_order]

    def to_state(self, players: list[PlayerId]) -> GameState:
        """A turn-0 state with ``players`` filling the first slots of ``fill_order``.

        Each player holds their base and gets `STARTING_UNITS` on and around it
        (never on a site or base plot hex), plus the economy's starting stocks. Every other site starts neutral with
        its tier's garrison (`GARRISON_FORCES`); the base sites of unfilled slots
        stay vacant (no settlement, nothing to capture) until someone is seated
        there. Raises `WorldGenError` if there are more players than slots or a
        player id repeats.
        """
        if len(players) > self.params.slots:
            raise WorldGenError(
                f"{len(players)} players for a {self.params.slots}-slot world"
            )
        if len(set(players)) != len(players):
            raise WorldGenError("player ids must be unique")
        base_owner = {
            self.capitals[self.fill_order[k]]: player
            for k, player in enumerate(players)
        }
        economy = WorldEconomy()
        settlements = [
            Settlement(coord=site.coord, controlled_by=base_owner.get(site.coord))
            for site in self.game_map.sites
            if site.kind is not SiteKind.BASE or site.coord in base_owner
        ]
        units = self._starting_units(players)
        for unit in units:
            unit.facing = default_facing(unit.coord, unit.owner, units)
        units.extend(self._garrisons({u.coord for u in units}, len(units) + 1))
        return GameState(
            turn=0,
            map=self.game_map,
            settlements=settlements,
            units=units,
            players=[Player(id=p, stocks=economy.starting_stocks) for p in players],
            config=GameConfig(
                unit_cap=WORLD_UNIT_CAP,
                settlement_repair=WORLD_SETTLEMENT_REPAIR,
                economy=economy,
                victory=VictoryConfig(turn_limit=1_000_000, city_elimination=False),
            ),
        )

    def _garrisons(self, taken: set[Axial], first_id: int) -> list[Unit]:
        """Every neutral site's garrison: its tier's first unit on the site, the
        rest on the nearest free open hexes."""
        index = self.game_map.index
        reserved = self.game_map.plot_hexes
        taken = set(taken)
        units: list[Unit] = []
        for site in self.game_map.sites:
            if site.kind is SiteKind.BASE:
                continue
            forces = GARRISON_FORCES[site.garrison]
            spots = [site.coord]
            if len(forces) > 1:
                spots += nearest_open(site.coord, index, reserved, taken)
            for (unit_type, hp), coord in zip(forces, spots, strict=False):
                taken.add(coord)
                units.append(
                    Unit(
                        id=first_id + len(units),
                        type=unit_type,
                        owner=NEUTRAL,
                        coord=coord,
                        hp=hp,
                    )
                )
        return units

    def _starting_units(self, players: list[PlayerId]) -> list[Unit]:
        """Place each player's opening force: the garrison on the capital, the
        rest on the nearest free open hexes (by distance, then axial order)."""
        index = self.game_map.index
        reserved = self.game_map.plot_hexes
        taken: set[Axial] = set()
        units: list[Unit] = []
        for k, player in enumerate(players):
            capital = self.capitals[self.fill_order[k]]
            spots = [capital] + nearest_open(capital, index, reserved, taken)
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


def nearest_open(
    center: Axial,
    index: dict[Axial, Hex],
    reserved: frozenset[Axial],
    taken: set[Axial],
) -> list[Axial]:
    """Free plains/woods/hills hexes near ``center``, nearest first (up to radius 3).

    ``reserved`` hexes are never used: pass `GameMap.plot_hexes`, so no unit is
    placed on a site or inside a base footprint. Raises `WorldGenError` if there
    is no room for a starting force.
    """
    open_terrain = {Terrain.PLAINS, Terrain.WOODS, Terrain.HILLS}
    spots = [
        c
        for c in within(center, 3)
        if c in index
        and index[c].terrain in open_terrain
        and c not in reserved
        and c not in taken
    ]
    spots.sort(key=lambda c: (distance(center, c), c.q, c.r))
    if len(spots) < len(STARTING_UNITS) - 1:
        raise WorldGenError(f"no room for a starting force around {center}")
    return spots


# --- Pipeline -------------------------------------------------------------------


@dataclass(frozen=True)
class _DraftSite:
    """A site as the generator places it: hex index, kind, garrison tier, level."""

    hex: int
    kind: SiteKind
    garrison: int
    level: int


@dataclass
class _Draft:
    """Mutable working state while a world is generated.

    ``land`` is fixed: its ``river`` marks river course hexes, ``lake`` lake hexes
    and ``water`` either. ``terrain`` starts as the land's and loses woods or
    hills under plots and sites. ``road_wiggle`` (in [0, 1]) scales
    off-road step costs. ``seats`` and ``bases`` are hex indices per slot: the
    seat town and the base anchor beside it. ``plots`` holds every hex kept clear
    so far (base footprints and site hexes). The counters feed `_stats`.
    """

    grid: HexGrid
    rng: random.Random
    land: Land
    terrain: list[Terrain]
    road_wiggle: list[float]
    road: list[bool] = field(default_factory=list)
    settlements: list[int] = field(default_factory=list)
    cities: list[bool] = field(default_factory=list)
    seats: list[int] = field(default_factory=list)
    territory: list[int] = field(default_factory=list)
    bases: list[int] = field(default_factory=list)
    plots: set[int] = field(default_factory=set)
    sites: list[_DraftSite] = field(default_factory=list)  # bases excluded
    converted_footprint_hexes: int = 0
    converted_site_hexes: int = 0
    widened_bases: int = 0
    widened_sites: int = 0
    sites_off_territory: int = 0

    def water(self, i: int) -> bool:
        """Whether hex ``i`` is river or lake."""
        return self.land.water[i]

    def passable(self, i: int) -> bool:
        """Whether a column may enter hex ``i``: dry land, or a bridge."""
        return not self.water(i) or self.road[i]

    def open_land(self) -> list[bool]:
        """Per hex: not river or lake (bridges count as water here)."""
        return [not w for w in self.land.water]

    def lay_road(
        self, start: int, goal: int, allowed: Callable[[int], bool] | None = None
    ) -> None:
        """Lay a least-cost road from ``start`` to ``goal`` (`_road_step_cost`),
        then drop the corner of any sharp turn it makes over new ground."""
        path = astar(self.grid, start, goal, self._road_step_cost, ROAD_REUSE, allowed)
        if path is None:
            raise WorldGenError(f"no road possible from hex {start} to hex {goal}")
        for i in self._without_sharp_turns(path):
            self.road[i] = True

    def _road_step_cost(self, v: int) -> float:
        """The price of a road entering hex ``v``.

        Existing road is cheap. A lake is closed. A river hex may be bridged only
        where the river runs plainly: exactly two river neighbours and no lake
        beside it, so never at a junction, a river end or a lake mouth. Other
        ground costs its terrain's `ROAD_COST`, scaled by the wiggle field, plus
        `ROAD_PARALLEL` beside an existing road.
        """
        if self.road[v]:
            return ROAD_REUSE
        land = self.land
        if land.lake[v]:
            return math.inf
        around = self.grid.neighbors[v]
        if land.river[v]:
            if any(land.lake[n] for n in around):
                return math.inf
            if sum(land.river[n] for n in around) != 2:
                return math.inf
            return ROAD_COST[Terrain.RIVER]
        scale = 1.0 - ROAD_WIGGLE / 2 + ROAD_WIGGLE * self.road_wiggle[v]
        cost = ROAD_COST[self.terrain[v]] * scale
        if any(self.road[n] for n in around):
            cost += ROAD_PARALLEL
        return cost

    def _without_sharp_turns(self, path: list[int]) -> list[int]:
        """``path`` with each new-ground corner ``b`` of a sharp turn ``a-b-c``
        (``a`` and ``c`` adjacent) removed; the path stays connected."""
        neighbors = self.grid.neighbors
        kept = list(path)
        k = 1
        while k < len(kept) - 1:
            if not self.road[kept[k]] and kept[k + 1] in neighbors[kept[k - 1]]:
                del kept[k]
                k = max(1, k - 1)
            else:
                k += 1
        return kept


def generate(params: WorldParams) -> GeneratedWorld:
    """Generate the world for ``params`` (pure; deterministic in ``params``)."""
    grid = HexGrid(params.width, params.height)
    rng = random.Random(
        f"hex-general-world:{params.seed}:{params.width}x{params.height}:{params.slots}"
    )
    land = build_land(
        grid,
        rng,
        hills_share=params.hills_share,
        woods_share=params.woods_share,
        river_share=params.river_share,
        hexes_per_lake=params.hexes_per_lake,
    )
    draft = _Draft(
        grid=grid,
        rng=rng,
        land=land,
        terrain=list(land.terrain),
        road_wiggle=rank_normalise(fbm(grid, rng, feature=10.0, octaves=2)),
        road=[False] * grid.size,
    )

    # One settlement per slot becomes a seat, on top of the density target, so
    # bases don't take neutral sites away (economy draft §03). A seat is a town,
    # so no start has a free city on its doorstep, and it gets no site of its own.
    count = grid.size // params.hexes_per_settlement + params.slots
    draft.settlements = _place_settlements(grid, rng, land, count)
    draft.cities = _pick_cities(grid, rng, land, draft.settlements, params.city_share)
    seat_picks = _choose_seats(draft, params.slots)
    for k in seat_picks:
        draft.cities[k] = False
    draft.seats = [draft.settlements[k] for k in seat_picks]

    for a, b in _road_plan(draft):
        draft.lay_road(draft.settlements[a], draft.settlements[b])
    _fix_up(draft)
    # Base plots first, so the sites keep clear of them.
    _place_bases(draft)
    _place_sites(draft)
    return _finish(draft, params)


def _town_appeal(grid: HexGrid, land: Land, i: int) -> float:
    """How good hex ``i`` is for a town (0 on water, the map edge and a spring's
    hills).

    Plains beat woods beat hills; a waterside hex gets a bonus; a hex ringed by
    rough ground, or deep in hill country, less.
    """
    if land.water[i] or grid.on_edge(i) or land.spring_hills[i]:
        return 0.0
    terrain = land.terrain[i]
    appeal = (
        1.0 if terrain is Terrain.PLAINS else 0.6 if terrain is Terrain.WOODS else 0.4
    )
    around = grid.neighbors[i]
    if any(land.water[n] for n in around):
        appeal *= 1.3
    if sum(land.terrain[n] in _ROUGH for n in around) >= 5:
        appeal *= 0.6
    return appeal * (1.12 - 0.4 * land.ruggedness[i] ** 2)


_ROUGH = (Terrain.WOODS, Terrain.HILLS)


def _place_settlements(
    grid: HexGrid, rng: random.Random, land: Land, count: int
) -> list[int]:
    """Best-candidate (Mitchell) sampling weighted by `_town_appeal`: of 16 random
    hexes with some appeal, take the one with the best spacing (capped, so
    appeal still counts in empty country) times appeal."""
    spacing = math.sqrt(grid.size / count)
    radius = int(2 * spacing) + 2
    nearest = [2.0 * spacing] * grid.size
    appeal = [_town_appeal(grid, land, i) for i in range(grid.size)]
    pool = [i for i in range(grid.size) if appeal[i] > 0]
    taken = [False] * grid.size
    chosen: list[int] = []
    for _ in range(count):
        candidates: list[int] = []
        while not candidates:
            candidates = [
                c for c in (rng.choice(pool) for _ in range(16)) if not taken[c]
            ]
        best = max(candidates, key=lambda c: min(nearest[c], 1.6 * spacing) * appeal[c])
        chosen.append(best)
        taken[best] = True
        for i in grid.window(best, radius):
            d = grid.distance(i, best)
            if d < nearest[i]:
                nearest[i] = d
    return chosen


def _pick_cities(
    grid: HexGrid,
    rng: random.Random,
    land: Land,
    settlements: list[int],
    share: float,
) -> list[bool]:
    """Mark ``share`` of ``settlements`` as cities by farthest-point sampling, so
    cities are spread-out hubs; open and waterside ground weighs a little more."""
    count = len(settlements)
    weight: list[float] = []
    for s in settlements:
        around = grid.neighbors[s]
        w = 1.25 if any(land.water[n] for n in around) else 1.0
        w *= 1.0 - 0.08 * sum(land.terrain[n] in _ROUGH for n in around)
        weight.append(w * rng.uniform(0.8, 1.2))
    cities = [False] * count
    nearest = [40.0] * count
    for _ in range(round(share * count)):
        pick = max(
            (k for k in range(count) if not cities[k]),
            key=lambda k: nearest[k] * weight[k],
        )
        cities[pick] = True
        for k in range(count):
            nearest[k] = min(
                nearest[k], grid.distance(settlements[pick], settlements[k])
            )
    return cities


def _choose_seats(draft: _Draft, slots: int) -> list[int]:
    """Pick one seat settlement per slot (positions in ``draft.settlements``).

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

    open_hex = draft.open_land()
    points = [sites[k] for k in chosen]
    for _ in range(_LLOYD_STEPS):
        points = _lloyd_step(grid, points, open_hex)
    return _snap_seats(draft, points, open_hex)


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


def _snap_seats(draft: _Draft, points: list[int], open_hex: list[bool]) -> list[int]:
    """Seat each relaxed point on a nearby settlement, keeping seats apart.

    Each region takes its settlement nearest the point (cities preferred) that is
    at least 60% of the ideal lattice spacing from seats already taken; if no
    settlement qualifies, the nearest one anywhere still free.
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

    Territories are each seat's nearest land in steps over passable hexes, so
    they do not depend on roads; the fix-ups lay roads only inside territories
    and so never move a border.
    """
    for _ in range(_FIXUP_ROUNDS):
        passable = [draft.passable(i) for i in range(draft.grid.size)]
        draft.territory, _ = multi_source_bfs(draft.grid, draft.seats, passable)
        changed = _road_every_border(draft)
        changed = _join_territory_roads(draft) or changed
        changed = _settle_sparse_territories(draft) or changed
        if not changed:
            return
    raise WorldGenError(f"fairness fix-ups did not settle in {_FIXUP_ROUNDS} rounds")


def _road_every_border(draft: _Draft) -> bool:
    """Lay a seat-to-seat road across any real border no road crosses."""
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
            draft.seats[p],
            draft.seats[q],
            allowed=_inside(territory, p, q),
        )
        changed = True
    return changed


def _join_territory_roads(draft: _Draft) -> bool:
    """Connect every road fragment in a territory that holds a settlement to its
    seat's network, by a road inside the territory.

    A fragment without a settlement (a through-road clipping the territory) is
    left alone: joining those laid parallel double roads along borders.
    """
    changed = False
    settlements = set(draft.settlements)
    for p, seat in enumerate(draft.seats):
        for fragment in _road_fragments(draft, p):
            towns = fragment & settlements
            if seat in fragment or not towns:
                continue
            draft.lay_road(min(towns), seat, allowed=_inside(draft.territory, p))
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

    Every settlement but a seat yields one site (`_place_sites`), so this keeps
    the sites, the ownership-bearing things, shared fairly. The town goes on the
    open hex farthest from every settlement, and is roaded to the seat inside the
    territory. One town per territory per round.
    """
    grid = draft.grid
    counts = _settlements_per_territory(draft)
    floor = max(1, int(0.5 * statistics.median(counts)))
    lacking = [p for p, c in enumerate(counts) if c < floor]
    if not lacking:
        return False
    _, spacing = multi_source_bfs(grid, draft.settlements, draft.open_land())
    occupied = set(draft.settlements)
    for p in lacking:
        spots = [
            i
            for i in range(grid.size)
            if draft.territory[i] == p
            and not draft.water(i)
            and not draft.land.spring_hills[i]
            and not grid.on_edge(i)
            and i not in occupied
        ]
        if not spots:
            raise WorldGenError(f"territory {p} has no room for another settlement")
        town = max(spots, key=lambda i: (spacing[i], -i))
        draft.settlements.append(town)
        draft.cities.append(False)
        occupied.add(town)
        draft.lay_road(
            town,
            draft.seats[p],
            allowed=_inside(draft.territory, p),
        )
    return True


def _settlements_per_territory(draft: _Draft) -> list[int]:
    """Settlements in each territory, not counting its seat."""
    counts = [0] * len(draft.seats)
    seats = set(draft.seats)
    for s in draft.settlements:
        p = draft.territory[s]
        if p >= 0 and s not in seats:
            counts[p] += 1
    return counts


# --- Base plots ------------------------------------------------------------------


def _footprint(grid: HexGrid, anchor: int) -> list[int] | None:
    """The `BASE_FOOTPRINT` hexes for a base anchored at ``anchor``, or ``None`` if
    any of them is off the map."""
    hexes = [
        grid.index_of(grid.q[anchor] + d.q, grid.r[anchor] + d.r)
        for d in BASE_FOOTPRINT
    ]
    return None if -1 in hexes else hexes


def _clear(draft: _Draft, i: int, settlements: set[int]) -> bool:
    """Whether a base plot or a site may use hex ``i``: off the edge ring, not a
    river, bridge, lake, town or city, not a spring's hills, no road, and not
    already kept clear."""
    return not (
        draft.grid.on_edge(i)
        or draft.water(i)
        or draft.land.spring_hills[i]
        or draft.road[i]
        or i in settlements
        or i in draft.plots
    )


def _beside_road(draft: _Draft, i: int) -> bool:
    return any(draft.road[n] for n in draft.grid.neighbors[i])


def _to_plains(draft: _Draft, hexes: list[int]) -> int:
    """Turn woods or hills among ``hexes`` into plains; return how many changed."""
    changed = 0
    for i in hexes:
        if draft.terrain[i] is not Terrain.PLAINS:
            draft.terrain[i] = Terrain.PLAINS
            changed += 1
    return changed


def _place_bases(draft: _Draft) -> None:
    """Choose each slot's base anchor near its seat and keep its footprint clear.

    All four `BASE_FOOTPRINT` hexes must be clear (`_clear`), inside the slot's
    territory and within 3 hexes of the seat (5 if nothing fits in 3), and at
    least one must touch a road. The nearest anchor wins, then the one needing
    the fewest woods or hills turned to plains, then a seeded coin. Raises
    `WorldGenError` if a seat has no room for a base.
    """
    grid = draft.grid
    settlements = set(draft.settlements)
    for p, seat in enumerate(draft.seats):
        chosen: list[int] | None = None
        for radius in _BASE_RADII:
            options: list[tuple[int, list[int]]] = []
            for anchor in grid.disc(seat, radius):
                plot = _footprint(grid, anchor)
                if plot is None or not all(
                    _clear(draft, i, settlements)
                    and draft.territory[i] == p
                    and grid.distance(i, seat) <= radius
                    for i in plot
                ):
                    continue
                if any(_beside_road(draft, i) for i in plot):
                    options.append((anchor, plot))
            if options:
                if radius != _BASE_RADII[0]:
                    draft.widened_bases += 1
                _, chosen = min(
                    options,
                    key=lambda option: (
                        grid.distance(option[0], seat),
                        sum(draft.terrain[i] is not Terrain.PLAINS for i in option[1]),
                        draft.rng.random(),
                    ),
                )
                break
        if chosen is None:
            raise WorldGenError(
                f"slot {p} has no clear base plot within {_BASE_RADII[-1]} hexes of "
                f"its seat at ({grid.q[seat]}, {grid.r[seat]})"
            )
        draft.bases.append(chosen[0])
        draft.plots.update(chosen)
        draft.converted_footprint_hexes += _to_plains(draft, chosen)


# --- Sites -----------------------------------------------------------------------


def _place_sites(draft: _Draft) -> None:
    """Every city's site on its hex, and every other town's site beside it.

    Seats get no site (their slot's base is beside them). Kinds are dealt per
    territory (`_deal_kinds`); each town's site then takes a hex (`_site_hex`)
    and a level (`_draw_level`), and its garrison tier follows its distance to
    the nearest base (`_garrison_tier`).
    """
    grid = draft.grid
    seats = set(draft.seats)
    kinds = _deal_kinds(draft)
    taken = set(draft.bases)
    placed: list[tuple[int, SiteKind, int]] = []
    for s, city in zip(draft.settlements, draft.cities, strict=True):
        if city:
            placed.append((s, SiteKind.CITY, 1))
            taken.add(s)
            draft.plots.add(s)
    for s, city in zip(draft.settlements, draft.cities, strict=True):
        if city or s in seats:
            continue
        hex_ = _site_hex(draft, s, taken)
        taken.add(hex_)
        draft.plots.add(hex_)
        placed.append((hex_, kinds[s], _draw_level(draft.rng)))
    draft.sites = [
        _DraftSite(
            hex=h,
            kind=kind,
            garrison=_garrison_tier(
                kind, min(grid.distance(h, b) for b in draft.bases), level
            ),
            level=level,
        )
        for h, kind, level in placed
    ]


def _deal_kinds(draft: _Draft) -> dict[int, SiteKind]:
    """The site kind of every town but a seat (economy draft §03).

    Kinds are dealt per territory by the quotas above, measured from the slot's
    base; a town outside every territory is a farm.
    """
    grid = draft.grid
    rng = draft.rng
    seats = set(draft.seats)
    kinds: dict[int, SiteKind] = {}
    towns_by_slot: dict[int, list[int]] = {}
    for s, city in zip(draft.settlements, draft.cities, strict=True):
        if city or s in seats:
            continue
        kinds[s] = SiteKind.FARM
        p = draft.territory[s]
        if p >= 0:
            towns_by_slot.setdefault(p, []).append(s)

    for p, towns in sorted(towns_by_slot.items()):
        home = draft.bases[p]
        dist = {t: grid.distance(t, home) for t in towns}
        free = sorted(towns, key=lambda t: (dist[t], t))

        def deal(kind: SiteKind, eligible: Callable[[int], bool], ideal: int) -> None:
            pool = [t for t in free if eligible(t)] or list(free)
            if not pool:
                return
            pick = min(pool, key=lambda t: (abs(dist[t] - ideal), rng.random()))
            free.remove(pick)
            kinds[pick] = kind

        lo, hi = _FACTORY_BAND
        deal(SiteKind.FACTORY, lambda t: lo <= dist[t] <= hi, _FACTORY_IDEAL)
        near = lambda t: dist[t] <= _NEAR_RESOURCE  # noqa: E731
        deal(SiteKind.OIL_FIELD, near, rng.randint(0, _NEAR_RESOURCE))
        deal(SiteKind.STEEL_MILL, near, rng.randint(0, _NEAR_RESOURCE))
        if len(towns) >= _SECOND_RESOURCE_TOWNS:
            second = rng.choice((SiteKind.OIL_FIELD, SiteKind.STEEL_MILL))
            deal(second, lambda t: dist[t] > _NEAR_RESOURCE, rng.randint(13, 24))
    return kinds


def _site_hex(draft: _Draft, town: int, sites: set[int]) -> int:
    """The hex for ``town``'s site, which is then made plains.

    It must be clear (`_clear`), within 2 hexes of the town (3 if nothing fits
    in 2), beside the town or a road, and in the town's territory. A town wedged
    into a corner of its territory (a three-way border, often behind a river) may
    have no such hex: only then may the site stand in a neighbouring territory
    within 3 hexes. Preferred, in order: plains, beside a road, beside the town,
    not beside another of ``sites``, nearest the town, then a seeded coin. Raises
    `WorldGenError` if nothing fits.
    """
    grid = draft.grid
    settlements = set(draft.settlements)
    territory = draft.territory[town]
    attempts = [(radius, True) for radius in _SITE_RADII] + [(_SITE_RADII[-1], False)]
    for radius, at_home in attempts:
        best: tuple[tuple[bool, bool, bool, bool, int, float], int] | None = None
        for i in grid.disc(town, radius):
            if not _clear(draft, i, settlements):
                continue
            if at_home and draft.territory[i] != territory:
                continue
            by_road = _beside_road(draft, i)
            by_town = grid.distance(i, town) == 1
            if not (by_road or by_town):
                continue
            key = (
                draft.terrain[i] is not Terrain.PLAINS,
                not by_road,
                not by_town,
                any(n in sites for n in grid.neighbors[i]),
                grid.distance(i, town),
                draft.rng.random(),
            )
            if best is None or key < best[0]:
                best = (key, i)
        if best is not None:
            if radius != _SITE_RADII[0]:
                draft.widened_sites += 1
            if not at_home:
                draft.sites_off_territory += 1
            draft.converted_site_hexes += _to_plains(draft, [best[1]])
            return best[1]
    raise WorldGenError(
        f"no clear hex for the site of the town at ({grid.q[town]}, {grid.r[town]}) "
        f"within {_SITE_RADII[-1]} hexes"
    )


def _draw_level(rng: random.Random) -> int:
    """A site level drawn by `SITE_LEVEL_WEIGHTS`."""
    levels = [level for level, _ in SITE_LEVEL_WEIGHTS]
    weights = [weight for _, weight in SITE_LEVEL_WEIGHTS]
    return rng.choices(levels, weights=weights)[0]


def _garrison_tier(kind: SiteKind, distance_to_base: int, level: int) -> int:
    """Tier 1–4 for a neutral site: farther out, richer and higher-level is harder.

    A level-3 site's tier is one above what distance and kind give (provisional
    tuning, 2026-11-10).
    """
    tier = (
        1
        if distance_to_base <= _TIER_NEAR
        else 2 if distance_to_base <= _TIER_MID else 3
    )
    if kind in (SiteKind.OIL_FIELD, SiteKind.STEEL_MILL):
        tier = max(tier + 1, 2)
    elif kind in (SiteKind.FACTORY, SiteKind.CITY):
        tier = max(tier, 3)
    if level == MAX_SITE_LEVEL:
        tier += 1
    return min(tier, 4)


# --- Assembly ---------------------------------------------------------------------


def _finish(draft: _Draft, params: WorldParams) -> GeneratedWorld:
    """Freeze the draft into a `GeneratedWorld` with provinces and stats.

    The map lists the bases first, in slot order, then the other sites.
    """
    grid = draft.grid
    terrain = list(draft.terrain)
    for i in range(grid.size):
        if draft.land.river[i] and draft.road[i]:
            terrain[i] = Terrain.BRIDGE
    for s, city in zip(draft.settlements, draft.cities, strict=True):
        terrain[s] = Terrain.CITY if city else Terrain.TOWN

    coords = [Axial(q=grid.q[i], r=grid.r[i]) for i in range(grid.size)]
    sites = [
        MapSite(coord=coords[b], kind=SiteKind.BASE, garrison=0) for b in draft.bases
    ] + [
        MapSite(
            coord=coords[site.hex],
            kind=site.kind,
            garrison=site.garrison,
            level=site.level,
        )
        for site in draft.sites
    ]
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
    province = multi_source_dijkstra(
        grid, draft.bases + [site.hex for site in draft.sites], entry_cost
    )

    game_map = GameMap(
        width=grid.width,
        height=grid.height,
        hexes=[
            Hex(coord=coords[i], terrain=terrain[i], road=draft.road[i])
            for i in range(grid.size)
        ],
        sites=sites,
    )
    return GeneratedWorld(
        params=params,
        game_map=game_map,
        settlements=[coords[s] for s in draft.settlements],
        capitals=[coords[b] for b in draft.bases],
        seats=[coords[s] for s in draft.seats],
        fill_order=_fill_order(grid, draft.bases),
        territory=list(draft.territory),
        province=province,
        stats=_stats(draft, terrain),
    )


def _fill_order(grid: HexGrid, capitals: list[int]) -> list[int]:
    """Slots ordered so every prefix is spread out: start nearest the map centre,
    then repeatedly take the capital (base anchor) farthest from those taken."""
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
            for k, a in enumerate(draft.bases)
            for b in draft.bases[k + 1 :]
        ),
        default=0,
    )
    levels = Counter(
        site.level for site in draft.sites if site.kind is not SiteKind.CITY
    )

    def largest_patch(kind: Terrain) -> int:
        groups = patches(grid, [t is kind for t in terrain])
        return max((len(g) for g in groups), default=0)

    return {
        "hexes": grid.size,
        "share_woods": round(shares[Terrain.WOODS] / grid.size, 4),
        "share_hills": round(shares[Terrain.HILLS] / grid.size, 4),
        "share_river": round(shares[Terrain.RIVER] / grid.size, 4),
        "share_lake": round(shares[Terrain.LAKE] / grid.size, 4),
        "bridges": shares[Terrain.BRIDGE],
        "lakes": draft.land.lakes,
        "lakes_dropped": draft.land.lakes_dropped,
        "rivers": draft.land.rivers,
        "woods_patch_max": largest_patch(Terrain.WOODS),
        "hills_patch_max": largest_patch(Terrain.HILLS),
        "share_road": round(sum(draft.road) / grid.size, 4),
        "settlements": len(draft.settlements),
        "cities": sum(draft.cities),
        "slots": len(draft.bases),
        "min_capital_distance": spacing,
        "sites": len(draft.sites) + len(draft.bases),
        "sites_level_1": levels[1],
        "sites_level_2": levels[2],
        "sites_level_3": levels[3],
        "site_hexes_converted": draft.converted_site_hexes,
        "footprint_hexes_converted": draft.converted_footprint_hexes,
        "sites_widened": draft.widened_sites,
        "sites_off_territory": draft.sites_off_territory,
        "bases_widened": draft.widened_bases,
        "territory_hexes_min": min(territory_size.values()),
        "territory_hexes_max": max(territory_size.values()),
        "territory_settlements_min": min(per_territory),
        "territory_settlements_median": statistics.median(per_territory),
        "territory_settlements_max": max(per_territory),
        "unclaimed_passable_hexes": sum(
            1 for i in range(grid.size) if draft.passable(i) and draft.territory[i] < 0
        ),
    }
