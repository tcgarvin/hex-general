"""Columns: packing, marching, stacking and deploying (WORLD_SPEC §4).

Rule constants under test come from `COLUMN_RULES`: 16 pulses per turn, packing
takes pulses 1–4, road steps cost 1 pulse and off-road steps 2, stacks hold 6
columns, and a column halts within 3 hexes of a hostile unit.
"""

from __future__ import annotations

from typing import Any

from app.engine.events import (
    BarrageResolved,
    ColumnHeld,
    ColumnStuck,
    CombatResolved,
    DeployCause,
    MoveBlocked,
    MoveBlockedReason,
    UnitDeployed,
    UnitMoved,
    UnitPacked,
)
from app.engine.execution import resolve_execution
from app.engine.hex_coords import distance
from app.engine.models import DeployOrder, GameState, MarchOrder, Posture, Unit
from app.engine.movement import resolve_movement
from app.engine.stats import COLUMN_RULES

from engine.helpers import ax, barrage, build, move

ROW = "." * 30
ROAD = "#" * 30


def march(unit_id: int, *steps: tuple[int, int], deploy: bool = False) -> MarchOrder:
    return MarchOrder(
        unit_id=unit_id, path=[ax(q, r) for q, r in steps], deploy_on_arrival=deploy
    )


def east(start: int, end: int, r: int = 0) -> list[tuple[int, int]]:
    """Path hexes (start..end inclusive) along the q axis of row ``r``."""
    return [(q, r) for q in range(start, end + 1)]


def unit(state: GameState, unit_id: int) -> Unit:
    return next(u for u in state.units if u.id == unit_id)


def pack(state: GameState, ids: dict[int, tuple[int, int]]) -> GameState:
    """Turn the given units into columns at the given hexes (stacking allowed)."""
    for u in state.units:
        if u.id in ids:
            u.posture = Posture.COLUMN
            u.coord = ax(*ids[u.id])
    return state


def of_kind(events: list[Any], kind: type) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


# --- Packing and marching speed ---------------------------------------------


def test_deployed_unit_packs_then_marches_twelve_road_hexes() -> None:
    state = build(
        [ROW], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}], roads=[ROAD]
    )
    new, events = resolve_movement(state, [march(1, *east(1, 25))])

    packed = of_kind(events, UnitPacked)
    assert [(e.pulse, e.unit_id) for e in packed] == [(COLUMN_RULES.pack_pulses + 1, 1)]
    assert unit(new, 1).coord == ax(12, 0)
    assert unit(new, 1).posture is Posture.COLUMN


def test_off_road_steps_cost_two_pulses() -> None:
    state = build([ROW], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    new, _ = resolve_movement(state, [march(1, *east(1, 25))])
    assert unit(new, 1).coord == ax(6, 0)


def test_packed_column_marches_from_pulse_one() -> None:
    state = pack(
        build(
            [ROW], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}], roads=[ROAD]
        ),
        {1: (0, 0)},
    )
    new, events = resolve_movement(state, [march(1, *east(1, 25))])
    assert unit(new, 1).coord == ax(16, 0)
    assert not of_kind(events, UnitPacked)


def test_deploy_on_arrival_is_clean() -> None:
    state = pack(
        build(
            [ROW], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}], roads=[ROAD]
        ),
        {1: (0, 0)},
    )
    new, events = resolve_movement(state, [march(1, *east(1, 3), deploy=True)])

    (deployed,) = of_kind(events, UnitDeployed)
    assert deployed.cause is DeployCause.ARRIVED and not deployed.disordered
    assert unit(new, 1).posture is Posture.DEPLOYED
    assert unit(new, 1).coord == ax(3, 0)
    assert not unit(new, 1).disordered


def test_deploy_order_unpacks_in_place() -> None:
    state = pack(
        build([ROW], [{"type": "infantry", "owner": "blue", "q": 4, "r": 0}]),
        {1: (4, 0)},
    )
    new, events = resolve_movement(state, [DeployOrder(unit_id=1)])
    (deployed,) = of_kind(events, UnitDeployed)
    assert deployed.cause is DeployCause.ORDERED
    assert unit(new, 1).posture is Posture.DEPLOYED and unit(new, 1).coord == ax(4, 0)


# --- Stacking -----------------------------------------------------------------


def test_full_stack_makes_a_column_wait() -> None:
    units = [{"type": "infantry", "owner": "blue", "q": q, "r": 0} for q in range(7)]
    stack = {i: (5, 0) for i in range(1, COLUMN_RULES.stack_cap + 1)}
    state = pack(build([ROW], units, roads=[ROAD]), {**stack, 7: (4, 0)})

    new, events = resolve_movement(state, [march(7, (5, 0), (6, 0))])

    assert unit(new, 7).coord == ax(4, 0)
    assert of_kind(events, ColumnHeld)
    assert not of_kind(events, MoveBlocked)


def test_columns_stack_under_a_friendly_deployed_unit() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
    ]
    state = pack(build([ROW], units, roads=[ROAD]), {2: (0, 0)})
    new, _ = resolve_movement(state, [march(2, (1, 0), (2, 0))])
    assert unit(new, 2).coord == ax(2, 0)
    assert unit(new, 1).coord == ax(2, 0)


# --- Contact and auto-deploy --------------------------------------------------


def test_column_halts_outside_deploy_distance_and_deploys_disordered() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "infantry", "owner": "red", "q": 12, "r": 0},
    ]
    state = pack(build([ROW], units, roads=[ROAD]), {1: (0, 0)})
    new, events = resolve_movement(state, [march(1, *east(1, 11))])

    halt_q = 12 - COLUMN_RULES.deploy_distance - 1
    (deployed,) = of_kind(events, UnitDeployed)
    assert deployed.cause is DeployCause.CONTACT and deployed.disordered
    assert unit(new, 1).coord == ax(halt_q, 0)
    assert unit(new, 1).posture is Posture.DEPLOYED and unit(new, 1).disordered


def test_stationary_column_deploys_when_approached() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "armor", "owner": "red", "q": 6, "r": 0},
    ]
    state = pack(build([ROW], units), {1: (0, 0)})
    new, events = resolve_movement(state, [move(2, *east(2, 5)[::-1])])

    (deployed,) = of_kind(events, UnitDeployed)
    assert deployed.unit_id == 1 and deployed.disordered
    # Red reaches (3, 0) — within 3 — at the end of pulse 3; blue deploys at pulse 4.
    assert deployed.pulse == 4
    assert unit(new, 1).posture is Posture.DEPLOYED


def test_crowded_column_spills_to_the_nearest_free_hex() -> None:
    grid = [ROW, ROW, ROW]
    units = [
        {"type": "infantry", "owner": "blue", "q": 5, "r": 1},
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
    ]
    state = pack(build(grid, units), {2: (5, 1)})
    new, events = resolve_movement(state, [DeployOrder(unit_id=2)])

    (deployed,) = of_kind(events, UnitDeployed)
    assert deployed.origin == ax(5, 1) and deployed.dest != ax(5, 1)
    assert deployed.dest == unit(new, 2).coord
    assert distance(deployed.dest, ax(5, 1)) <= COLUMN_RULES.spill_radius


def test_column_with_no_room_stays_packed() -> None:
    units = [{"type": "infantry", "owner": "blue", "q": q, "r": 0} for q in range(5)]
    units.append({"type": "infantry", "owner": "blue", "q": 6, "r": 0})
    # Hexes (0..4, 0) all hold deployed units; (5, 0) and (6, 0) are 3+ away.
    state = pack(build(["......."], units), {6: (2, 0)})
    new, events = resolve_movement(state, [DeployOrder(unit_id=6)])

    assert of_kind(events, ColumnStuck)
    assert unit(new, 6).posture is Posture.COLUMN


# --- Packing restrictions -----------------------------------------------------


def test_engaged_unit_cannot_pack() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "infantry", "owner": "red", "q": 1, "r": 0},
    ]
    state = build([ROW], units)
    new, events = resolve_movement(state, [march(1, (0, 1))])

    blocked = of_kind(events, MoveBlocked)
    assert [b.reason for b in blocked] == [MoveBlockedReason.ENGAGED_CANNOT_PACK]
    assert unit(new, 1).posture is Posture.DEPLOYED


def test_packing_is_interrupted_when_a_hostile_comes_near() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "infantry", "owner": "red", "q": 5, "r": 0},
    ]
    state = build([ROW], units)
    new, events = resolve_movement(
        state, [march(1, *east(1, 3)), move(2, (4, 0), (3, 0))]
    )

    reasons = [b.reason for b in of_kind(events, MoveBlocked)]
    assert MoveBlockedReason.PACK_INTERRUPTED in reasons
    assert unit(new, 1).posture is Posture.DEPLOYED
    assert not unit(new, 1).disordered


def test_attacked_packing_unit_stays_deployed() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "armor", "owner": "red", "q": 2, "r": 0},
    ]
    state = build([ROW], units)
    new, events = resolve_movement(
        state, [march(1, (0, 1), (0, 2)), move(2, (1, 0), (0, 0))]
    )

    assert of_kind(events, CombatResolved)
    reasons = [b.reason for b in of_kind(events, MoveBlocked)]
    assert MoveBlockedReason.PACK_INTERRUPTED in reasons
    assert unit(new, 1).posture is Posture.DEPLOYED


# --- Combat against columns and disorder --------------------------------------


def test_barrage_strikes_every_column_in_the_stack() -> None:
    grid = [ROW, ROW, ROW]
    units = [
        {"type": "infantry", "owner": "blue", "q": 10, "r": 1},
        {"type": "infantry", "owner": "blue", "q": 11, "r": 1},
        {"type": "infantry", "owner": "blue", "q": 12, "r": 1},
        {"type": "artillery", "owner": "red", "q": 0, "r": 1},
    ]
    state = pack(build(grid, units), {1: (3, 1), 2: (3, 1), 3: (3, 1)})
    new, events = resolve_execution(state, [barrage(4, 3, 1)])

    (shot,) = of_kind(events, BarrageResolved)
    assert shot.hit is not None
    hits = [shot.hit, *shot.extra_hits]
    assert sorted(h.unit_id for h in hits) == [1, 2, 3]
    assert all(h.defense_penalty == COLUMN_RULES.column_defense_penalty for h in hits)
    # Survivors deploy, disordered; only one can keep the struck hex.
    deployed = of_kind(events, UnitDeployed)
    assert {d.unit_id for d in deployed} == {1, 2, 3}
    assert all(d.cause is DeployCause.BARRAGED and d.disordered for d in deployed)
    assert len({unit(new, i).coord for i in (1, 2, 3)}) == 3


def test_disordered_unit_cannot_attack() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "infantry", "owner": "red", "q": 1, "r": 0},
    ]
    state = build([ROW], units)
    unit(state, 1).disordered = True
    _, events = resolve_movement(state, [move(1, (1, 0))])
    assert [b.reason for b in of_kind(events, MoveBlocked)] == [
        MoveBlockedReason.DISORDERED
    ]


def test_disordered_defender_takes_more_damage() -> None:
    def armor_hits(disordered: bool) -> int:
        units = [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "armor", "owner": "red", "q": 1, "r": 0},
        ]
        state = build([ROW], units)
        unit(state, 1).disordered = disordered
        new, _ = resolve_movement(state, [move(2, (0, 0))])
        return 10 - unit(new, 1).hp

    penalty = COLUMN_RULES.disorder_defense_penalty
    assert armor_hits(True) == armor_hits(False) + penalty


def test_disorder_lasts_through_the_next_turn() -> None:
    units = [
        {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
        {"type": "infantry", "owner": "red", "q": 12, "r": 0},
    ]
    state = pack(build([ROW], units, roads=[ROAD]), {1: (0, 0)})

    after_contact, _ = resolve_execution(state, [march(1, *east(1, 11))])
    assert unit(after_contact, 1).disordered
    after_next, _ = resolve_execution(after_contact, [])
    assert not unit(after_next, 1).disordered


def test_columns_do_not_capture() -> None:
    units = [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}]
    town = [{"q": 2, "r": 0}]
    grid = ["..t" + "." * 27]
    state = pack(build(grid, units, roads=[ROAD], settlements=town), {1: (0, 0)})
    new, events = resolve_execution(state, [march(1, (1, 0), (2, 0))])

    assert unit(new, 1).coord == ax(2, 0)
    assert new.settlement_at(ax(2, 0)).controlled_by is None  # type: ignore[union-attr]


def test_games_without_columns_keep_v01_pulse_count() -> None:
    units = [{"type": "armor", "owner": "blue", "q": 0, "r": 0}]
    state = build([ROW], units)
    _, events = resolve_movement(state, [move(1, *east(1, 4))])
    assert max(e.pulse for e in of_kind(events, UnitMoved)) == 4
