"""World generator gates (WORLD_SPEC §6).

Small worlds (12–24 slots) stand in for the 200-slot map: the generator is the
same code at every size, and these run in a few seconds. Each gate is a fairness
or validity property the spec asks of every generated world.
"""

from __future__ import annotations

import math
import statistics
from collections import deque

from collections import Counter

import pytest

from app.engine.economy_rules import GARRISON_FORCES, SiteKind, WorldEconomy
from app.engine.execution import resolve_execution
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import BASE_FOOTPRINT, NEUTRAL, base_footprint
from app.engine.routing import find_route
from app.engine.stats import TERRAIN_STATS, Terrain
from app.world.generate import (
    SITE_LEVEL_WEIGHTS,
    STARTING_UNITS,
    GeneratedWorld,
    WorldGenError,
    WorldParams,
    _garrison_tier,
    generate,
)
from app.world.preview import render

SEEDS = (1, 2, 3)


@pytest.fixture(scope="module", params=SEEDS)
def world(request: pytest.FixtureRequest) -> GeneratedWorld:
    return generate(WorldParams.for_slots(12, request.param))


@pytest.fixture(scope="module")
def big_world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(24, 7))


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


def test_same_params_same_world() -> None:
    params = WorldParams.for_slots(12, 5)
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
    assert 0.10 < world.stats["share_woods"] < 0.20
    assert 0.06 < world.stats["share_hills"] < 0.14
    assert 0.0 < world.stats["share_river"] < 0.06


def test_every_settlement_is_on_one_road_network(world: GeneratedWorld) -> None:
    network = _road_component(world, world.seats[0])
    assert set(world.settlements) <= network


def test_each_start_region_has_one_road_network(world: GeneratedWorld) -> None:
    hexes = world.game_map.hexes
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
        assert seen == roads, f"slot {slot} has road fragments cut off from its seat"


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


def test_params_reject_unusable_worlds() -> None:
    with pytest.raises(WorldGenError, match="too few"):
        WorldParams(width=20, height=20, slots=10, seed=1)
    with pytest.raises(WorldGenError, match="at least"):
        WorldParams(width=8, height=40, slots=1, seed=1)


def test_preview_is_a_png(world: GeneratedWorld) -> None:
    png = render(world, scale=2)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
