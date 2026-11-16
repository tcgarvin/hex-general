"""World towns are held (user, 2026-11-15): capture, sight, and no economy.

Every world town has a `Settlement`. A seated player starts with their seat
town; the rest start neutral, with no militia. Infantry or armor entering a town
takes it, as for a site. A held town earns nothing, adds nothing to army size and
gives its holder a flat 8-hex view that woods do not block.
"""

from __future__ import annotations

import pytest

from app.ai.world import WorldPolicy
from app.engine.events import MilitiaMustered, SettlementCaptured
from app.engine.execution import resolve_execution
from app.engine.fog import filter_events, redact_state
from app.engine.hex_coords import Axial, distance, within
from app.engine.models import GameState, Unit
from app.engine.stats import Terrain, UnitType
from app.engine.visibility import visible_hexes
from app.engine.world_economy import resolve_world_economy
from app.world.generate import GeneratedWorld, WorldParams, generate

PLAYERS = ["p0", "p1"]


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(4, seed=7))


def _quiet_town(state: GameState) -> Axial:
    """A neutral town no unit stands within 2 hexes of, outside every militia
    zone (the seat town of a vacant slot, in the fixture world)."""
    garrisoned = [s.coord for s in state.map.sites if s.garrison > 0]
    for town in state.map.towns:
        settlement = state.settlement_at(town)
        assert settlement is not None
        if settlement.controlled_by is not None:
            continue
        if any(distance(u.coord, town) <= 2 for u in state.units):
            continue
        if any(distance(s, town) <= 1 for s in garrisoned):
            continue
        return town
    raise AssertionError("no quiet town in the fixture world")


def _hold(state: GameState, coord: Axial, player: str) -> GameState:
    held = state.fork()
    settlement = held.settlement_at(coord)
    assert settlement is not None
    settlement.controlled_by = player
    return held


def test_every_town_has_one_settlement(world: GeneratedWorld) -> None:
    state = world.to_state(PLAYERS)
    coords = [s.coord for s in state.settlements]
    assert len(coords) == len(set(coords))
    assert set(state.map.towns) <= set(coords)


def test_infantry_entering_a_neutral_town_takes_it(world: GeneratedWorld) -> None:
    state = world.to_state(PLAYERS)
    town = _quiet_town(state)
    placed = state.fork()
    raider = next(
        u for u in placed.units if u.owner == "p1" and u.type is UnitType.INFANTRY
    )
    raider.coord = town

    after, events = resolve_execution(placed, [])

    captured = [e for e in events if isinstance(e, SettlementCaptured)]
    assert captured == [
        SettlementCaptured(
            coord=town,
            settlement=Terrain.TOWN,
            site=None,
            captured_by="p1",
            previous_owner=None,
            unit_id=raider.id,
        )
    ]
    settlement = after.settlement_at(town)
    assert settlement is not None and settlement.controlled_by == "p1"
    # The capture reaches the captor's replay like any site capture.
    assert captured[0] in filter_events(events, after, "p1")


def test_a_held_town_earns_nothing_and_adds_no_army_size(
    world: GeneratedWorld,
) -> None:
    state = world.to_state(PLAYERS)
    held = _hold(state, _quiet_town(state), "p0")

    plain, _ = resolve_world_economy(state, [])
    with_town, _ = resolve_world_economy(held, [])

    assert with_town.player("p0").stocks == plain.player("p0").stocks
    assert held.unit_cap_for("p0") == state.unit_cap_for("p0")


def test_towns_never_muster_militia(world: GeneratedWorld) -> None:
    state = world.to_state(PLAYERS)
    towns = set(state.map.towns)
    for _ in range(4):
        state, events = resolve_execution(state, [])
        mustered = [e for e in events if isinstance(e, MilitiaMustered)]
        assert not any(e.post in towns for e in mustered)
    assert not any(u.post in towns for u in state.units)


def test_a_held_town_shows_its_holder_enemies_eight_hexes_out_through_woods(
    world: GeneratedWorld,
) -> None:
    state = world.to_state(PLAYERS)
    town = _quiet_town(state)
    own = [u.coord for u in state.units if u.owner == "p0"]
    taken = {u.coord for u in state.units}
    spot = next(
        c
        for c in within(town, 8)
        if distance(c, town) == 8
        and c in state.map.index
        and state.map.index[c].terrain is Terrain.PLAINS
        and c not in taken
        and all(distance(c, o) > 6 for o in own)
    )
    scout = Unit(id=999, type=UnitType.INFANTRY, owner="p1", coord=spot, hp=10)
    # A ring of woods between the town and the scout: unit sight would stop there.
    hexes = [
        (
            h.model_copy(update={"terrain": Terrain.WOODS})
            if distance(h.coord, town) == 4
            else h
        )
        for h in state.map.hexes
    ]
    wooded = state.map.model_copy(update={"hexes": hexes})
    blind = state.model_copy(update={"map": wooded, "units": [*state.units, scout]})
    held = _hold(blind, town, "p0")

    assert spot not in visible_hexes(blind, "p0")
    assert spot in visible_hexes(held, "p0")
    assert scout.id in {u.id for u in redact_state(held, "p0").units}
    assert scout.id not in {u.id for u in redact_state(blind, "p0").units}


def test_world_bots_plan_with_towns_held_on_both_sides(world: GeneratedWorld) -> None:
    state = world.to_state(PLAYERS)
    held = _hold(state, _quiet_town(state), "p1")
    for slot in PLAYERS:
        plan = WorldPolicy(seed=1).plan(redact_state(held, slot), slot)
        assert plan.orders
