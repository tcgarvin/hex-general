"""World generator gates (WORLD_SPEC §6).

Small worlds (12–24 slots) stand in for the 200-slot map: the generator is the
same code at every size, and these run in a few seconds. Each gate is a fairness
or validity property the spec asks of every generated world.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, deque

import pytest

from app.engine.economy_rules import GARRISON_FORCES, SiteKind, WorldEconomy
from app.engine.execution import resolve_execution
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import BASE_FOOTPRINT, NEUTRAL, base_footprint
from app.engine.routing import find_route
from app.engine.stats import TERRAIN_STATS, Terrain
from app.world.generate import (
    DEFAULT_ROAD_STYLE,
    ROAD_STYLES,
    SITE_LEVEL_WEIGHTS,
    STARTING_UNITS,
    GeneratedWorld,
    RoadStyle,
    WorldGenError,
    WorldParams,
    _garrison_tier,
    generate,
)
from app.world.grid import HexGrid, patches
from app.world.land import LAKE_SIZES, SPRING_EDGE_MARGIN
from app.world.preview import STYLES, Style, render
from app.world.road_metrics import measure
from app.world.roads import BRIDGE_RAMPS

SEEDS = (1, 2, 3)
SWEEP_SEEDS = range(1, 21)
RIVER_LIKE = (Terrain.RIVER, Terrain.BRIDGE)
WATER = (Terrain.RIVER, Terrain.BRIDGE, Terrain.LAKE)
SETTLEMENT_GROUND = (Terrain.TOWN, Terrain.CITY)


@pytest.fixture(
    scope="module",
    params=[(seed, style) for style in ROAD_STYLES for seed in SEEDS],
    ids=lambda p: f"{p[1]}-{p[0]}",
)
def world(request: pytest.FixtureRequest) -> GeneratedWorld:
    seed, style = request.param
    return generate(WorldParams.for_slots(12, seed, road_style=style))


@pytest.fixture(scope="module")
def big_world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(24, 7))


@pytest.fixture(scope="module", params=ROAD_STYLES)
def sweep(request: pytest.FixtureRequest) -> list[GeneratedWorld]:
    """Twenty 12-slot worlds plus one 40-slot world in each road style, for
    gates that must hold on every seed rather than on average."""
    style = request.param
    worlds = [
        generate(WorldParams.for_slots(12, seed, road_style=style))
        for seed in SWEEP_SEEDS
    ]
    return worlds + [generate(WorldParams.for_slots(40, 2, road_style=style))]


def _layout(world: GeneratedWorld) -> tuple[HexGrid, list[Terrain], list[bool]]:
    """The world's grid geometry (its hexes are in grid index order), terrain and
    road flags per index."""
    game_map = world.game_map
    grid = HexGrid(game_map.width, game_map.height)
    return grid, [h.terrain for h in game_map.hexes], [h.road for h in game_map.hexes]


def _undrained_water(world: GeneratedWorld) -> int:
    """Water hexes not connected through water to water on the map edge."""
    grid, terrain, _ = _layout(world)
    reached = [grid.on_edge(i) and terrain[i] in WATER for i in range(grid.size)]
    stack = [i for i in range(grid.size) if reached[i]]
    while stack:
        u = stack.pop()
        for v in grid.neighbors[u]:
            if not reached[v] and terrain[v] in WATER:
                reached[v] = True
                stack.append(v)
    return sum(t in WATER and not r for t, r in zip(terrain, reached))


def _ideal_spacing(world: GeneratedWorld) -> float:
    """Capital spacing on a perfect triangular lattice of equal start regions."""
    passable = sum(TERRAIN_STATS[h.terrain].passable for h in world.game_map.hexes)
    return math.sqrt(passable / world.params.slots / 0.75)


def _road_component(world: GeneratedWorld, start: Axial) -> set[Axial]:
    index = world.game_map.index
    seen = {start}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for n in neighbors(here):
            if n not in seen and n in index and index[n].road:
                seen.add(n)
                queue.append(n)
    return seen


def _fingerprint(world: GeneratedWorld) -> str:
    """A digest of everything a world holds: hexes, sites, places, layout, stats."""
    digest = hashlib.sha256()
    for h in world.game_map.hexes:
        digest.update(
            f"{h.coord.q},{h.coord.r},{h.terrain.value},{int(h.road)};".encode()
        )
    for s in world.game_map.sites:
        digest.update(
            f"{s.coord.q},{s.coord.r},{s.kind.value},{s.garrison},{s.level};".encode()
        )
    for coords in (world.settlements, world.capitals, world.seats):
        digest.update(",".join(f"{c.q}:{c.r}" for c in coords).encode())
        digest.update(b"|")
    layout = [world.fill_order, world.territory, world.province, world.stats]
    digest.update(json.dumps(layout, sort_keys=True).encode())
    return digest.hexdigest()[:16]


def test_new_worlds_default_to_natural_roads() -> None:
    # The user picked natural roads for new worlds on 2026-11-11.
    assert DEFAULT_ROAD_STYLE == "natural"
    assert WorldParams.for_slots(12, 1).road_style == "natural"
    assert WorldParams(width=40, height=40, slots=1, seed=1).road_style == "natural"


@pytest.mark.parametrize(
    ("slots", "seed", "expected"),
    [
        (12, 1, "fc32d4d67b75fd1d"),
        (12, 2, "cd6a530e90fff0e3"),
        (12, 3, "b74c706ececcbbea"),
        (24, 7, "c4e64302c377bc0b"),
    ],
)
def test_direct_roads_on_request_regenerate_earlier_worlds_byte_identical(
    slots: int, seed: int, expected: str
) -> None:
    # Digests recorded before the natural road style existed (2026-11-11), when
    # direct was the only style: asking for it must keep reproducing those worlds.
    params = WorldParams.for_slots(slots, seed, road_style="direct")
    assert _fingerprint(generate(params)) == expected


@pytest.mark.parametrize(
    ("slots", "seed", "expected"),
    [
        (12, 1, "191fe607d2dd40de"),
        (12, 2, "fe94dd073374cee8"),
        (12, 3, "8f5c4f6949fdbd0b"),
        (24, 7, "a7e966dedf7e9ecf"),
    ],
)
def test_default_natural_worlds_are_pinned(
    slots: int, seed: int, expected: str
) -> None:
    # Digests recorded when natural became the default (2026-11-11). A change here
    # changes every new world for a seed: make it on purpose and re-record.
    assert _fingerprint(generate(WorldParams.for_slots(slots, seed))) == expected


@pytest.mark.parametrize("style", ROAD_STYLES)
def test_same_params_same_world(style: RoadStyle) -> None:
    params = WorldParams.for_slots(12, 5, road_style=style)
    a, b = generate(params), generate(params)
    assert a.game_map.hexes == b.game_map.hexes
    assert a.game_map.sites == b.game_map.sites
    assert a.settlements == b.settlements
    assert a.capitals == b.capitals
    assert a.seats == b.seats
    assert a.fill_order == b.fill_order
    assert a.stats == b.stats


def test_different_seeds_differ() -> None:
    a = generate(WorldParams.for_slots(12, 5))
    b = generate(WorldParams.for_slots(12, 6))
    assert a.game_map.hexes != b.game_map.hexes


def test_map_layers_are_consistent(world: GeneratedWorld) -> None:
    for hex_ in world.game_map.hexes:
        if hex_.terrain is Terrain.BRIDGE:
            assert hex_.road
        if hex_.terrain is Terrain.RIVER:
            assert not hex_.road
    index = world.game_map.index
    settlement_terrain = {index[s].terrain for s in world.settlements}
    assert settlement_terrain <= {Terrain.TOWN, Terrain.CITY}
    assert all(index[c].terrain is Terrain.PLAINS for c in world.capitals)
    assert all(index[s].terrain is Terrain.TOWN for s in world.seats)
    assert len(world.capitals) == len(world.seats) == world.params.slots
    assert sorted(world.fill_order) == list(range(world.params.slots))
    assert len(world.territory) == len(world.province) == len(world.game_map.hexes)


def test_terrain_mix_is_near_the_requested_shares(world: GeneratedWorld) -> None:
    params = world.params
    assert abs(world.stats["share_woods"] - params.woods_share) < 0.02
    assert abs(world.stats["share_hills"] - params.hills_share) < 0.015
    assert abs(world.stats["share_river"] - params.river_share) < 0.006
    assert world.stats["share_lake"] > 0
    assert world.stats["lakes"] > 0 and world.stats["rivers"] > 0


def test_stats_report_the_terrain_layout(world: GeneratedWorld) -> None:
    grid, terrain, _ = _layout(world)
    lakes = patches(grid, [t is Terrain.LAKE for t in terrain])
    assert world.stats["lakes"] == len(lakes)
    assert world.stats["share_lake"] == round(
        sum(t is Terrain.LAKE for t in terrain) / grid.size, 4
    )
    assert world.stats["share_river"] == round(
        sum(t is Terrain.RIVER for t in terrain) / grid.size, 4
    )


@pytest.mark.parametrize("kind", [Terrain.WOODS, Terrain.HILLS])
def test_no_woods_or_hills_patch_is_over_thirty_hexes(
    world: GeneratedWorld, kind: Terrain
) -> None:
    grid, terrain, _ = _layout(world)
    sizes = [len(p) for p in patches(grid, [t is kind for t in terrain])]
    assert max(sizes) <= 30
    assert world.stats[f"{kind.value}_patch_max"] == max(sizes)


def test_lakes_stay_small(world: GeneratedWorld) -> None:
    # Lakes grow to at most the top `LAKE_SIZES` size; filling an enclosed hole
    # could add a hex or two, but lakes grow compactly from their centre and the
    # 80-seed sweep never exceeded the drawn size, so the bound is the size itself.
    biggest = max(high for (_, high), _ in LAKE_SIZES)
    grid, terrain, _ = _layout(world)
    for lake in patches(grid, [t is Terrain.LAKE for t in terrain]):
        assert 3 <= len(lake) <= biggest
        assert all(grid.edge_distance(i) >= 3 for i in lake)


def test_rivers_are_one_hex_chains_without_sharp_turns(world: GeneratedWorld) -> None:
    grid, terrain, _ = _layout(world)
    for i in range(grid.size):
        if terrain[i] not in RIVER_LIKE:
            continue
        course = [n for n in grid.neighbors[i] if terrain[n] in RIVER_LIKE]
        # Two along its own course, plus one where a tributary joins.
        assert 1 <= len(course) <= 3, i
        # Two river neighbours that touch would be a sharp turn here, or a
        # triangle of mutually adjacent river hexes.
        for k, a in enumerate(course):
            for b in course[k + 1 :]:
                assert b not in grid.neighbors[a], (i, a, b)


def test_all_water_drains_off_the_map(sweep: list[GeneratedWorld]) -> None:
    for world in sweep:
        assert _undrained_water(world) == 0, world.params


def test_every_lake_has_an_outflow(world: GeneratedWorld) -> None:
    grid, terrain, _ = _layout(world)
    for lake in patches(grid, [t is Terrain.LAKE for t in terrain]):
        shore = {n for i in lake for n in grid.neighbors[i]}
        assert any(terrain[n] in RIVER_LIKE for n in shore), lake


def test_rivers_rise_at_springs_by_hills_or_run_off_the_map(
    sweep: list[GeneratedWorld],
) -> None:
    for world in sweep:
        grid, terrain, _ = _layout(world)
        for i in range(grid.size):
            if terrain[i] not in RIVER_LIKE:
                continue
            around = [terrain[n] for n in grid.neighbors[i]]
            ends_here = sum(t in RIVER_LIKE for t in around) == 1
            if not ends_here or Terrain.LAKE in around:
                continue  # mid-course, or a lake outflow's source or mouth
            if grid.on_edge(i):
                continue  # flows off the map
            assert Terrain.HILLS in around, (world.params, i)
            assert grid.edge_distance(i) >= SPRING_EDGE_MARGIN, (world.params, i)


def test_no_lake_carries_a_road_and_bridges_cross_plain_river(
    world: GeneratedWorld,
) -> None:
    grid, terrain, road = _layout(world)
    for i in range(grid.size):
        if terrain[i] is Terrain.LAKE:
            assert not road[i], i
        if terrain[i] is Terrain.BRIDGE:
            around = [terrain[n] for n in grid.neighbors[i]]
            assert sum(t in RIVER_LIKE for t in around) == 2, i
            assert Terrain.LAKE not in around, i


def test_no_passable_land_is_cut_off(sweep: list[GeneratedWorld]) -> None:
    for world in sweep:
        assert world.stats["unclaimed_passable_hexes"] == 0, world.params


def test_roads_turn_sharply_only_at_settlements(world: GeneratedWorld) -> None:
    # A road hex whose only two road neighbours touch is a sharp corner. Laid
    # roads drop those, but a road ends at a settlement, so a town beside a
    # through-road can join it at two adjacent hexes.
    grid, terrain, road = _layout(world)
    for i in range(grid.size):
        if not road[i] or terrain[i] in SETTLEMENT_GROUND:
            continue
        joined = [n for n in grid.neighbors[i] if road[n]]
        if len(joined) == 2:
            assert joined[1] not in grid.neighbors[joined[0]], i


def test_every_settlement_is_on_one_road_network(world: GeneratedWorld) -> None:
    network = _road_component(world, world.seats[0])
    assert set(world.settlements) <= network


def test_every_settlement_reaches_its_seat_by_road_inside_its_territory(
    world: GeneratedWorld,
) -> None:
    # Road fragments without a settlement (through-roads clipping a corner) may
    # stay cut off from the seat's network inside the territory.
    hexes = world.game_map.hexes
    position = {h.coord: i for i, h in enumerate(hexes)}
    for slot, seat in enumerate(world.seats):
        roads = {
            h.coord
            for i, h in enumerate(hexes)
            if h.road and world.territory[i] == slot
        }
        seen = {seat}
        queue = deque([seat])
        while queue:
            here = queue.popleft()
            for n in neighbors(here):
                if n in roads and n not in seen:
                    seen.add(n)
                    queue.append(n)
        towns = {s for s in world.settlements if world.territory[position[s]] == slot}
        assert towns <= seen, f"slot {slot} has settlements cut off from its seat"


def test_roads_cross_every_real_border(world: GeneratedWorld) -> None:
    index = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    hexes = world.game_map.hexes
    contacts: dict[tuple[int, int], int] = {}
    crossed: set[tuple[int, int]] = set()
    for i, h in enumerate(hexes):
        p = world.territory[i]
        for n in neighbors(h.coord):
            j = index.get(n)
            if j is None or p < 0 or world.territory[j] <= p:
                continue
            pair = (p, world.territory[j])
            contacts[pair] = contacts.get(pair, 0) + 1
            if h.road and hexes[j].road:
                crossed.add(pair)
    real = {pair for pair, count in contacts.items() if count >= 4}
    assert real <= crossed


def test_sites_are_shared_fairly_between_start_regions(
    world: GeneratedWorld,
) -> None:
    counts = [0] * world.params.slots
    index = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    for site in world.game_map.sites:
        if site.kind is not SiteKind.BASE:
            counts[world.territory[index[site.coord]]] += 1
    assert min(counts) >= max(1, int(0.5 * statistics.median(counts)))


def test_starts_have_similar_sites_nearby(big_world: GeneratedWorld) -> None:
    radius = _ideal_spacing(big_world) / 2
    others = [s.coord for s in big_world.game_map.sites if s.kind is not SiteKind.BASE]
    nearby = [
        sum(1 for s in others if distance(s, c) <= radius) for c in big_world.capitals
    ]
    assert min(nearby) >= 0.4 * statistics.median(nearby)


def test_capitals_are_spread_apart(world: GeneratedWorld) -> None:
    closest = min(
        distance(a, b)
        for k, a in enumerate(world.capitals)
        for b in world.capitals[k + 1 :]
    )
    assert closest >= 0.5 * _ideal_spacing(world)


def test_fill_order_spreads_the_first_slots(big_world: GeneratedWorld) -> None:
    first, second = (big_world.capitals[s] for s in big_world.fill_order[:2])
    assert distance(first, second) >= 2 * _ideal_spacing(big_world)


def test_capitals_are_reachable_by_column(world: GeneratedWorld) -> None:
    origin = world.capitals[0]
    for capital in world.capitals[1:]:
        assert find_route(world.game_map, origin, capital)


def test_to_state_seats_players_and_resolves_a_turn(world: GeneratedWorld) -> None:
    players = [f"p{k}" for k in range(5)]
    state = world.to_state(players)

    seated = {world.capitals[world.fill_order[k]]: p for k, p in enumerate(players)}
    vacant = set(world.capitals) - set(seated)
    sites = world.game_map.site_index
    for settlement in state.settlements:
        assert settlement.controlled_by == seated.get(settlement.coord)
        assert settlement.coord not in vacant
        assert settlement.coord in sites
    assert len(state.settlements) == len(sites) - len(vacant)
    player_units = [u for u in state.units if u.owner != NEUTRAL]
    assert len(player_units) == len(STARTING_UNITS) * len(players)
    assert len({u.coord for u in state.units}) == len(state.units)
    for unit in state.units:
        assert TERRAIN_STATS[state.map.index[unit.coord].terrain].passable
    on_bases = {u.coord: u.owner for u in state.units if u.coord in seated}
    assert on_bases == seated
    for player in state.players:
        assert player.stocks == WorldEconomy().starting_stocks

    next_state, _ = resolve_execution(state, [])
    assert next_state.turn == 1


def test_every_neutral_site_starts_garrisoned_by_tier(world: GeneratedWorld) -> None:
    state = world.to_state(["p0"])
    garrison = [u for u in state.units if u.owner == NEUTRAL]
    on_site = {u.coord: u for u in garrison}
    expected = 0
    for site in world.game_map.sites:
        if site.kind is SiteKind.BASE:
            assert site.garrison == 0
            continue
        assert 1 <= site.garrison <= 4
        forces = GARRISON_FORCES[site.garrison]
        expected += len(forces)
        first = on_site[site.coord]
        assert (first.type, first.hp) == forces[0]
    assert len(garrison) == expected


def test_cities_are_sites_and_each_other_town_has_one_beside_it(
    world: GeneratedWorld,
) -> None:
    index = world.game_map.index
    sites = world.game_map.site_index
    bases = {c for c, s in sites.items() if s.kind is SiteKind.BASE}
    assert bases == set(world.capitals)
    cities = {c for c in world.settlements if index[c].terrain is Terrain.CITY}
    assert cities == {c for c, s in sites.items() if s.kind is SiteKind.CITY}
    towns = [c for c in world.settlements if index[c].terrain is Terrain.TOWN]
    # One site per town but the seats (their slot's base stands beside them).
    town_sites = len(sites) - len(bases) - len(cities)
    assert town_sites == len(towns) - len(world.seats)


def test_no_site_but_a_city_stands_on_town_or_city_terrain(
    world: GeneratedWorld,
) -> None:
    index = world.game_map.index
    for site in world.game_map.sites:
        terrain = index[site.coord].terrain
        if site.kind is SiteKind.CITY:
            assert terrain is Terrain.CITY
        else:
            assert terrain not in (Terrain.TOWN, Terrain.CITY), site


def test_town_sites_stand_on_clear_plains_beside_a_town_or_road(
    world: GeneratedWorld,
) -> None:
    index = world.game_map.index
    pos = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    towns = [c for c in world.settlements if index[c].terrain is Terrain.TOWN]
    for site in world.game_map.sites:
        if site.kind in (SiteKind.BASE, SiteKind.CITY):
            continue
        hex_ = index[site.coord]
        assert hex_.terrain is Terrain.PLAINS and not hex_.road, site
        near = [n for n in neighbors(site.coord) if n in index]
        assert len(near) == 6, f"{site} is on the map edge"
        beside_town = any(index[n].terrain is Terrain.TOWN for n in near)
        assert beside_town or any(index[n].road for n in near), site
        town = min(towns, key=lambda t: distance(t, site.coord))
        assert distance(town, site.coord) <= 3, site
        assert world.territory[pos[site.coord]] >= 0


def test_every_slot_has_a_clear_four_hex_base_plot(world: GeneratedWorld) -> None:
    index = world.game_map.index
    pos = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    sites = world.game_map.site_index
    settled = {s.coord for s in world.to_state([]).settlements}
    plots: set[Axial] = set()
    assert len(BASE_FOOTPRINT) == 4
    for slot, (anchor, seat) in enumerate(zip(world.capitals, world.seats)):
        plot = base_footprint(anchor)
        assert plot[0] == anchor and sites[anchor].kind is SiteKind.BASE
        assert sites[anchor].level == 1 and sites[anchor].garrison == 0
        for coord in plot:
            hex_ = index[coord]
            assert hex_.terrain is Terrain.PLAINS and not hex_.road, coord
            assert sum(n in index for n in neighbors(coord)) == 6, "on the map edge"
            assert coord == anchor or coord not in sites
            assert coord not in settled
            assert world.territory[pos[coord]] == slot
            assert distance(coord, seat) <= 5
        assert any(
            index[n].road for coord in plot for n in neighbors(coord) if n in index
        ), f"slot {slot}'s base plot touches no road"
        assert plots.isdisjoint(plot), f"slot {slot}'s base plot overlaps another"
        plots.update(plot)
    assert plots <= world.game_map.plot_hexes


def test_towns_hold_no_settlement(world: GeneratedWorld) -> None:
    index = world.game_map.index
    state = world.to_state(["p0", "p1"])
    assert all(index[s.coord].terrain is not Terrain.TOWN for s in state.settlements)


def test_units_start_off_the_site_and_plot_hexes(world: GeneratedWorld) -> None:
    state = world.to_state(["p0", "p1", "p2"])
    sites = world.game_map.site_index
    for unit in state.units:
        if unit.coord in world.game_map.plot_hexes:
            # Only a garrison's first unit or a player's first unit, on its site.
            assert unit.coord in sites, unit


def test_site_levels_are_one_to_three_and_cities_and_bases_are_level_one(
    world: GeneratedWorld,
) -> None:
    for site in world.game_map.sites:
        assert 1 <= site.level <= 3
        if site.kind in (SiteKind.BASE, SiteKind.CITY):
            assert site.level == 1


def test_higher_site_levels_are_rarer(big_world: GeneratedWorld) -> None:
    levels = Counter(
        s.level
        for s in big_world.game_map.sites
        if s.kind not in (SiteKind.BASE, SiteKind.CITY)
    )
    total = sum(levels.values())
    assert total > 100 and set(levels) == {1, 2, 3}
    assert levels[1] > levels[2] > levels[3]
    for level, weight in SITE_LEVEL_WEIGHTS:
        assert abs(levels[level] / total - weight) < 0.1, levels


def test_a_level_three_site_has_a_tougher_garrison() -> None:
    assert _garrison_tier(SiteKind.FARM, 5, 1) == 1
    assert _garrison_tier(SiteKind.FARM, 5, 2) == 1
    assert _garrison_tier(SiteKind.FARM, 5, 3) == 2
    assert _garrison_tier(SiteKind.OIL_FIELD, 20, 3) == 4  # capped


def test_every_territory_gets_a_factory_oil_and_steel(world: GeneratedWorld) -> None:
    hex_pos = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    kinds_by_slot: dict[int, set[SiteKind]] = {}
    for site in world.game_map.sites:
        slot = world.territory[hex_pos[site.coord]]
        kinds_by_slot.setdefault(slot, set()).add(site.kind)
    needed = {SiteKind.FACTORY, SiteKind.OIL_FIELD, SiteKind.STEEL_MILL}
    for slot in range(world.params.slots):
        assert needed <= kinds_by_slot[slot], slot


def test_to_state_rejects_too_many_players(world: GeneratedWorld) -> None:
    with pytest.raises(WorldGenError, match="players"):
        world.to_state([f"p{k}" for k in range(world.params.slots + 1)])


def test_natural_roads_keep_the_land_towns_and_seats() -> None:
    direct = generate(WorldParams.for_slots(12, 4, road_style="direct"))
    natural = generate(WorldParams.for_slots(12, 4, road_style="natural"))

    def land(world: GeneratedWorld) -> list[Terrain]:
        # Bridges are river; sites and base plots may turn woods or hills to plains.
        return [
            Terrain.RIVER if h.terrain is Terrain.BRIDGE else h.terrain
            for h in world.game_map.hexes
        ]

    changed = sum(a != b for a, b in zip(land(direct), land(natural), strict=True))
    assert changed <= len(direct.game_map.sites) + 4 * direct.params.slots
    assert natural.seats == direct.seats
    first = direct.game_map.width * direct.game_map.height // 70 + 12
    assert natural.settlements[:first] == direct.settlements[:first]
    assert natural.game_map.hexes != direct.game_map.hexes


@pytest.mark.parametrize("seed", SEEDS)
def test_natural_roads_are_far_less_ruled_than_direct_ones(seed: int) -> None:
    direct = measure(generate(WorldParams.for_slots(12, seed, road_style="direct")))
    natural = measure(generate(WorldParams.for_slots(12, seed, road_style="natural")))
    assert natural.long_run_share < 0.5 * direct.long_run_share
    assert natural.run_max < direct.run_max
    # A modest price in length: road between neighbouring towns is at most ~15%
    # longer (7% at 200 slots), and travel from a base to its sites about as quick.
    assert natural.detour_median <= 1.15 * direct.detour_median
    assert natural.base_to_site_median <= direct.base_to_site_median + 2


def test_natural_bridges_run_along_their_ramps(sweep: list[GeneratedWorld]) -> None:
    # The bridge tiles bake their road ramps for three river shapes; a natural
    # road crosses only those, along the ramps, unless no such bridge reaches
    # (counted in `natural_road_fallbacks`).
    if sweep[0].params.road_style != "natural":
        pytest.skip("the direct style does not place bridges by their art")
    fitting = total = 0
    for world in sweep:
        grid, terrain, road = _layout(world)
        for i in range(grid.size):
            if terrain[i] is not Terrain.BRIDGE:
                continue
            around = grid.neighbor_in[i]
            course = tuple(
                d for d, n in enumerate(around) if n >= 0 and terrain[n] in RIVER_LIKE
            )
            roads = tuple(
                d
                for d, n in enumerate(around)
                if n >= 0 and road[n] and terrain[n] not in RIVER_LIKE
            )
            total += 1
            fitting += BRIDGE_RAMPS.get((course[0], course[1])) == roads
    assert total > 100
    assert fitting / total >= 0.9


def test_params_reject_unusable_worlds() -> None:
    with pytest.raises(WorldGenError, match="road_style"):
        WorldParams(width=40, height=40, slots=1, seed=1, road_style="winding")  # type: ignore[arg-type]
    with pytest.raises(WorldGenError, match="too few"):
        WorldParams(width=20, height=20, slots=10, seed=1)
    with pytest.raises(WorldGenError, match="at least"):
        WorldParams(width=8, height=40, slots=1, seed=1)
    with pytest.raises(WorldGenError, match="river_share"):
        WorldParams(width=40, height=40, slots=1, seed=1, river_share=1.5)
    with pytest.raises(WorldGenError, match="hexes_per_lake"):
        WorldParams(width=40, height=40, slots=1, seed=1, hexes_per_lake=0)


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("roads", [True, False])
def test_preview_is_a_png(world: GeneratedWorld, style: Style, roads: bool) -> None:
    png = render(world, scale=2, style=style, roads=roads)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_natural_preview_shows_terrain_not_territories(world: GeneratedWorld) -> None:
    natural = render(world, scale=2, style="natural")
    assert natural != render(world, scale=2, style="territory")
    assert natural != render(world, scale=2, style="natural", roads=False)


def test_a_town_cornered_in_its_territory_still_gets_a_site() -> None:
    # Seed 331's town at a three-way border behind a river has no clear hex of its
    # own territory within reach; its site stands in the neighbouring territory.
    world = generate(WorldParams.for_slots(12, 331))
    assert world.stats["sites_off_territory"] >= 1
    assert world.stats["sites"] == len(world.game_map.sites)
