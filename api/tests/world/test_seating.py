"""Seating players in a running world (WORLD_SPEC §10.1)."""

from __future__ import annotations

import pytest

from app.engine.hex_coords import distance
from app.world.generate import STARTING_UNITS, GeneratedWorld, WorldParams, generate
from app.world.seating import (
    START_CLEARANCE,
    WorldFull,
    open_slot,
    seat_player,
    slot_id,
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


def test_a_taken_capital_shuts_its_slot(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(0)])
    for settlement in state.settlements:
        if settlement.coord == world.slots[1]:
            settlement.controlled_by = slot_id(0)
    assert open_slot(state, world.slots) == 2


def test_a_full_world_refuses_newcomers(world: GeneratedWorld) -> None:
    state = world.to_state([slot_id(k) for k in range(4)])
    with pytest.raises(WorldFull):
        seat_player(state, world.slots)
