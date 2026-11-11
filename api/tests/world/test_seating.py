"""Seating players in a running world (WORLD_SPEC §10.1)."""

from __future__ import annotations

import pytest

from app.engine.hex_coords import distance
from app.engine.economy_rules import SiteKind, WorldEconomy
from app.engine.models import NEUTRAL, Settlement
from app.engine.stats import Terrain
from app.world.generate import STARTING_UNITS, GeneratedWorld, WorldParams, generate
from app.world.seating import (
    START_CLEARANCE,
    WorldFull,
    open_slot,
    seat_player,
    slot_id,
    unseat_player,
)


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(4, seed=7))


def test_slot_ids_follow_fill_position() -> None:
    assert [slot_id(k) for k in (0, 1, 41)] == ["s001", "s002", "s042"]


def test_a_newcomer_takes_the_first_open_slot(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(0), slot_id(1)])
    seated, player = seat_player(state, world.slots)

    assert player == slot_id(2)
    capital = world.slots[2]
    own = [u for u in seated.units if u.owner == player]
    assert [u.type for u in own] == list(STARTING_UNITS)
    assert own[0].coord == capital
    assert all(distance(u.coord, capital) <= 3 for u in own)
    assert {u.id for u in own}.isdisjoint({u.id for u in state.units})
    assert next(s for s in seated.settlements if s.coord == capital).controlled_by == (
        player
    )
    assert [p.id for p in seated.players] == [slot_id(0), slot_id(1), player]


def test_seating_leaves_the_input_state_untouched(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(0)])
    before = state.model_dump(mode="json")
    seat_player(state, world.slots)
    assert state.model_dump(mode="json") == before


def test_a_slot_with_an_army_near_its_capital_is_skipped(
    world: GeneratedWorld,
) -> None:
    state = world.to_state([slot_id(0)])
    intruder = next(u for u in state.units if u.owner == slot_id(0))
    intruder.coord = world.slots[1]
    assert distance(intruder.coord, world.slots[1]) <= START_CLEARANCE
    assert open_slot(state, world.slots) == 2


def test_a_founded_base_site_shuts_its_slot(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(0)])
    assert all(s.coord != world.slots[1] for s in state.settlements)
    state.settlements.append(Settlement(coord=world.slots[1], controlled_by=slot_id(0)))
    assert open_slot(state, world.slots) == 2


def test_garrisons_near_a_base_site_do_not_shut_it(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(0)])
    guard = next(u for u in state.units if u.owner == NEUTRAL)
    guard.coord = world.slots[1]
    assert open_slot(state, world.slots) == 1


def test_a_seated_player_founds_their_base_with_starting_stocks(
    world: GeneratedWorld,
) -> None:
    seated, player = seat_player(world.to_state([slot_id(0)]), world.slots)
    base = next(s for s in seated.settlements if s.coord == world.slots[1])
    assert base.controlled_by == player
    assert seated.player(player).stocks == WorldEconomy().starting_stocks


def test_a_newcomer_founds_on_open_ground_and_keeps_off_plot_hexes(
    world: GeneratedWorld,
) -> None:
    seated, player = seat_player(world.to_state([slot_id(0)]), world.slots)
    capital = world.slots[1]
    assert seated.map.index[capital].terrain is Terrain.PLAINS
    assert seated.map.site_index[capital].kind is SiteKind.BASE
    own = [u for u in seated.units if u.owner == player]
    assert own[0].coord == capital
    assert all(u.coord not in seated.map.plot_hexes for u in own[1:])
    assert len({u.coord for u in seated.units}) == len(seated.units)


def test_a_full_world_refuses_newcomers(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(k) for k in range(4)])
    with pytest.raises(WorldFull):
        seat_player(state, world.slots)


def test_unseating_a_newcomer_restores_the_world_and_reopens_the_slot(
    world: GeneratedWorld,
) -> None:
    state = world.to_state([slot_id(0), slot_id(1)])
    seated, player = seat_player(state, world.slots)
    before = seated.model_dump(mode="json")

    unseated = unseat_player(seated, world.slots, player)

    assert seated.model_dump(mode="json") == before
    assert unseated.model_dump(mode="json") == state.model_dump(mode="json")
    assert open_slot(unseated, world.slots) == 2
