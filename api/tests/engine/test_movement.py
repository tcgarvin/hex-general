"""Tests for the movement-pulse engine (roadmap Phase 2; SPEC §7.1, §8, §11).

The engine is the riskiest core, so these tests pin down behaviour mechanic by
mechanic and then assert whole event logs for multi-unit scenarios (the
"golden" traces the roadmap calls for). Maps are built through the real scenario
loader so terrain/road parsing is exercised end to end; unit ids are assigned
1..N in declaration order.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.events import (
    MoveBlocked,
    MoveBlockedReason,
    TrafficJam,
    UnitMoved,
    ZoneOfControlStop,
)
from app.engine.hex_coords import Axial
from app.engine.models import GameState, MoveOrder, Order, RestOrder
from app.engine.movement import EngineError, resolve_movement
from app.engine.scenario import load_scenario


def build(
    terrain: list[str],
    units: list[dict[str, Any]],
    roads: list[str] | None = None,
) -> GameState:
    """Build a turn-0 state from compact terrain/road grids and a unit list."""
    width = len(terrain[0])
    height = len(terrain)
    scenario: dict[str, Any] = {
        "name": "Movement Fixture",
        "description": "Synthetic map for movement tests.",
        "width": width,
        "height": height,
        "terrain": terrain,
        "settlements": [],
        "units": units,
        "players": sorted({u["owner"] for u in units}) or ["blue"],
        "victory": {"turn_limit": 10},
    }
    if roads is not None:
        scenario["roads"] = roads
    return load_scenario(scenario)


def at(state: GameState, q: int, r: int) -> int | None:
    """Return the id of the unit at ``(q, r)``, or None."""
    unit = state.unit_at(Axial(q=q, r=r))
    return unit.id if unit is not None else None


def ax(q: int, r: int) -> Axial:
    return Axial(q=q, r=r)


def move(unit_id: int, *steps: tuple[int, int]) -> MoveOrder:
    return MoveOrder(unit_id=unit_id, path=[ax(q, r) for q, r in steps])


# --- Basic path following ---------------------------------------------------


def test_unit_follows_full_path_within_budget() -> None:
    # Infantry (move 2) walks two plains hexes east along row 0.
    state = build(["....."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0))])

    assert at(new_state, 2, 0) == 1
    assert events == [
        UnitMoved(pulse=1, unit_id=1, origin=ax(0, 0), dest=ax(1, 0)),
        UnitMoved(pulse=2, unit_id=1, origin=ax(1, 0), dest=ax(2, 0)),
    ]


def test_movement_capped_by_move_stat() -> None:
    # Infantry has move 2: a 3-hex path only advances two hexes, no 3rd pulse.
    state = build(["......"], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0), (3, 0))])

    assert at(new_state, 2, 0) == 1
    assert [e.pulse for e in events] == [1, 2]


def test_armor_moves_four_hexes() -> None:
    state = build(["......"], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    new_state, events = resolve_movement(
        state, [move(1, (1, 0), (2, 0), (3, 0), (4, 0))]
    )

    assert at(new_state, 4, 0) == 1
    assert len(events) == 4


def test_input_state_is_not_mutated() -> None:
    state = build(["....."], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    resolve_movement(state, [move(1, (1, 0), (2, 0))])
    assert at(state, 0, 0) == 1  # original unmoved


def test_unit_without_order_stays_put() -> None:
    state = build(["..."], [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}])
    new_state, events = resolve_movement(state, [])
    assert at(new_state, 1, 0) == 1
    assert events == []


def test_rest_order_does_not_move() -> None:
    state = build(["..."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    new_state, events = resolve_movement(state, [RestOrder(unit_id=1)])
    assert at(new_state, 0, 0) == 1
    assert events == []


# --- Roads (SPEC §3.3) ------------------------------------------------------


def test_road_bonus_grants_extra_step() -> None:
    # Infantry (move 2) on an all-road path reaches a third hex.
    state = build(
        ["...."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        roads=["####"],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0), (3, 0))])

    assert at(new_state, 3, 0) == 1
    assert [e.pulse for e in events] == [1, 2, 3]


def test_no_road_bonus_when_one_hex_off_road() -> None:
    # The last hex of the path is not a road, so the whole path forfeits +1.
    state = build(
        ["...."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        roads=["###."],
    )
    new_state, _ = resolve_movement(state, [move(1, (1, 0), (2, 0), (3, 0))])
    assert at(new_state, 2, 0) == 1  # stopped at budget 2, never reached (3,0)


def test_no_road_bonus_when_origin_off_road() -> None:
    state = build(
        ["...."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        roads=[".###"],
    )
    new_state, _ = resolve_movement(state, [move(1, (1, 0), (2, 0), (3, 0))])
    assert at(new_state, 2, 0) == 1


# --- Rivers and bridges (SPEC §3.4) -----------------------------------------


def test_river_is_impassable_and_stops_unit() -> None:
    # Column 1 is a river at row 1; the unit tries to cross south.
    state = build(
        ["...", "~~~", "..."],
        [{"type": "armor", "owner": "blue", "q": 1, "r": 0}],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 1), (1, 2))])

    assert at(new_state, 1, 0) == 1  # never left the north bank
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(1, 0),
            target=ax(1, 1),
            reason=MoveBlockedReason.IMPASSABLE,
        )
    ]


def test_bridge_is_passable() -> None:
    # A bridged road corridor at column 1 lets the unit cross the river.
    state = build(
        ["...", "~=~", "..."],
        [{"type": "armor", "owner": "blue", "q": 1, "r": 0}],
        roads=[".#.", ".#.", ".#."],
    )
    new_state, _ = resolve_movement(state, [move(1, (1, 1), (1, 2))])
    assert at(new_state, 1, 2) == 1


def test_off_map_step_is_blocked() -> None:
    state = build(["..."], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    new_state, events = resolve_movement(state, [move(1, (-1, 0))])
    assert at(new_state, 0, 0) == 1
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(0, 0),
            target=ax(-1, 0),
            reason=MoveBlockedReason.OFF_MAP,
        )
    ]


# --- Stacking & blocking (SPEC §8.1) ----------------------------------------


def test_blocked_by_stationary_friendly_unit() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert at(new_state, 0, 0) == 1  # unit 1 never moved
    assert at(new_state, 1, 0) == 2  # unit 2 still there
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(0, 0),
            target=ax(1, 0),
            reason=MoveBlockedReason.OCCUPIED,
        )
    ]


def test_move_into_stationary_enemy_is_combat_seam() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert at(new_state, 0, 0) == 1  # no damage, no entry in Phase 2
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(0, 0),
            target=ax(1, 0),
            reason=MoveBlockedReason.ENEMY,
        )
    ]


# --- Follow-the-leader (simultaneous vacate-and-fill) -----------------------


def test_follow_the_leader_chain_resolves_in_one_pulse() -> None:
    # Three units in a row; the leader steps into empty space and the followers
    # fill the vacated hexes in the same pulse.
    state = build(
        ["....."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
        ],
    )
    orders: list[Order] = [
        move(1, (1, 0)),
        move(2, (2, 0)),
        move(3, (3, 0)),
    ]
    new_state, events = resolve_movement(state, orders)

    assert at(new_state, 1, 0) == 1
    assert at(new_state, 2, 0) == 2
    assert at(new_state, 3, 0) == 3
    pulse1 = [e for e in events if e.pulse == 1]
    assert all(isinstance(e, UnitMoved) for e in pulse1)
    assert {e.unit_id for e in pulse1 if isinstance(e, UnitMoved)} == {1, 2, 3}


def test_swap_deadlocks_and_both_stop() -> None:
    # Two adjacent friendly units try to swap hexes — a cycle that cannot
    # resolve without stacking, so neither moves (documented v0.1 limitation).
    state = build(
        [".."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (0, 0))])

    assert at(new_state, 0, 0) == 1
    assert at(new_state, 1, 0) == 2
    assert all(isinstance(e, MoveBlocked) for e in events)
    assert {e.reason for e in events if isinstance(e, MoveBlocked)} == {
        MoveBlockedReason.OCCUPIED
    }


# --- Friendly traffic jams (SPEC §8.2) --------------------------------------


def test_friendly_traffic_jam_stops_all_involved() -> None:
    # Two friendly units converge on the empty centre hex in pulse 1.
    state = build(
        ["....."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 4, "r": 0},
        ],
    )
    new_state, events = resolve_movement(
        state, [move(1, (1, 0), (2, 0)), move(2, (3, 0), (2, 0))]
    )

    # Both advance once, then jam on (2,0) and stay on their pulse-1 hexes.
    assert at(new_state, 1, 0) == 1
    assert at(new_state, 3, 0) == 2
    jams = [e for e in events if isinstance(e, TrafficJam)]
    assert jams == [TrafficJam(pulse=2, coord=ax(2, 0), unit_ids=[1, 2])]


def test_enemy_contest_for_empty_hex_is_combat_seam() -> None:
    state = build(
        ["..."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (1, 0))])

    assert at(new_state, 0, 0) == 1  # neither entered the contested hex
    assert at(new_state, 2, 0) == 2
    assert {e.reason for e in events if isinstance(e, MoveBlocked)} == {
        MoveBlockedReason.CONTESTED
    }


# --- Zones of control (SPEC §8.3) -------------------------------------------


def test_unit_halts_on_entering_enemy_zoc() -> None:
    # Blue armor (move 4) heads east along row 0; red sits at (2,1). Reaching
    # (2,0) in pulse 2 puts blue adjacent to red, so it halts there instead of
    # continuing to (3,0)/(4,0).
    state = build(
        ["......", "......"],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 1},
        ],
    )
    new_state, events = resolve_movement(
        state, [move(1, (1, 0), (2, 0), (3, 0), (4, 0))]
    )

    assert at(new_state, 2, 0) == 1  # halted at the ZOC, did not reach (3,0)
    zoc = [e for e in events if isinstance(e, ZoneOfControlStop)]
    assert zoc == [ZoneOfControlStop(pulse=2, unit_id=1, coord=ax(2, 0))]
    assert not any(e.pulse > 2 for e in events)


def test_unit_may_leave_a_zoc_it_started_in() -> None:
    # Blue starts at (1,0), adjacent to red at (0,0). Its first step to (0,1)
    # (still adjacent to red) succeeds, then ZOC halts it — it is not frozen in
    # place from the start.
    state = build(
        ["..", "..", ".."],
        [
            {"type": "armor", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (0, 1), (0, 2))])

    assert at(new_state, 0, 1) == 1
    assert any(isinstance(e, UnitMoved) for e in events)
    assert any(isinstance(e, ZoneOfControlStop) for e in events)


# --- Order validation -------------------------------------------------------


def test_non_contiguous_path_raises() -> None:
    state = build(["...."], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    with pytest.raises(EngineError, match="not adjacent"):
        resolve_movement(state, [move(1, (2, 0))])


def test_duplicate_order_for_unit_raises() -> None:
    state = build(["...."], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    with pytest.raises(EngineError, match="more than one order"):
        resolve_movement(state, [move(1, (1, 0)), move(1, (1, 0))])


def test_order_for_unknown_unit_raises() -> None:
    state = build(["...."], [{"type": "armor", "owner": "blue", "q": 0, "r": 0}])
    with pytest.raises(EngineError, match="unknown unit"):
        resolve_movement(state, [move(99, (1, 0))])
