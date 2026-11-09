"""World generator gates (WORLD_SPEC §6).

Small worlds (12–24 slots) stand in for the 200-slot map: the generator is the
same code at every size, and these run in a few seconds. Each gate is a fairness
or validity property the spec asks of every generated world.
"""

from __future__ import annotations

import math
import statistics
from collections import deque

import pytest

from app.engine.economy_rules import GARRISON_FORCES, SiteKind, WorldEconomy
from app.engine.execution import resolve_execution
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import NEUTRAL
from app.engine.routing import find_route
from app.engine.stats import TERRAIN_STATS, Terrain
from app.world.generate import (
    STARTING_UNITS,
    GeneratedWorld,
    WorldGenError,
    WorldParams,
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
    assert a.settlements == b.settlements
    assert a.capitals == b.capitals
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
    settlement_terrain = {world.game_map.index[s].terrain for s in world.settlements}
    assert settlement_terrain <= {Terrain.TOWN, Terrain.CITY}
    assert all(world.game_map.index[c].terrain is Terrain.CITY for c in world.capitals)
    assert len(world.capitals) == world.params.slots
    assert sorted(world.fill_order) == list(range(world.params.slots))
    assert len(world.territory) == len(world.province) == len(world.game_map.hexes)


def test_terrain_mix_is_near_the_requested_shares(world: GeneratedWorld) -> None:
    assert 0.10 < world.stats["share_woods"] < 0.20
    assert 0.06 < world.stats["share_hills"] < 0.14
    assert 0.0 < world.stats["share_river"] < 0.06


def test_every_settlement_is_on_one_road_network(world: GeneratedWorld) -> None:
    network = _road_component(world, world.capitals[0])
    assert set(world.settlements) <= network


def test_each_start_region_has_one_road_network(world: GeneratedWorld) -> None:
    hexes = world.game_map.hexes
    for slot, capital in enumerate(world.capitals):
        roads = {
            h.coord
            for i, h in enumerate(hexes)
            if h.road and world.territory[i] == slot
        }
        seen = {capital}
        queue = deque([capital])
        while queue:
            here = queue.popleft()
            for n in neighbors(here):
                if n in roads and n not in seen:
                    seen.add(n)
                    queue.append(n)
        assert seen == roads, f"slot {slot} has road fragments cut off from its capital"


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


def test_settlements_are_shared_fairly_between_start_regions(
    world: GeneratedWorld,
) -> None:
    counts = [0] * world.params.slots
    capitals = set(world.capitals)
    index = {h.coord: i for i, h in enumerate(world.game_map.hexes)}
    for s in world.settlements:
        if s not in capitals:
            counts[world.territory[index[s]]] += 1
    assert min(counts) >= max(1, int(0.5 * statistics.median(counts)))


def test_starts_have_similar_settlements_nearby(big_world: GeneratedWorld) -> None:
    radius = _ideal_spacing(big_world) / 2
    nearby = [
        sum(1 for s in big_world.settlements if s != c and distance(s, c) <= radius)
        for c in big_world.capitals
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
    for settlement in state.settlements:
        assert settlement.controlled_by == seated.get(settlement.coord)
        assert settlement.coord not in vacant
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


def test_sites_cover_every_settlement_and_bases_are_the_capitals(
    world: GeneratedWorld,
) -> None:
    sites = world.game_map.site_index
    assert set(sites) == set(world.settlements)
    bases = {c for c, s in sites.items() if s.kind is SiteKind.BASE}
    assert bases == set(world.capitals)


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
