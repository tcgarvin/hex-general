"""Tests for the movement-pulse engine (roadmap Phase 2; SPEC §7.1, §8, §11).

The engine is the riskiest core, so these tests pin down behaviour mechanic by
mechanic and then assert whole event logs for multi-unit scenarios (the
"golden" traces the roadmap calls for). Maps are built through the real scenario
loader so terrain/road parsing is exercised end to end; unit ids are assigned
1..N in declaration order.
"""

from __future__ import annotations

import pytest

from app.engine.events import (
    CombatResolved,
    MoveBlocked,
    MoveBlockedReason,
    TrafficJam,
    UnitMoved,
)
from app.engine.models import Order, RestOrder
from app.engine.movement import EngineError, resolve_movement
from engine.helpers import at, ax, build, move

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
    assert [e.pulse for e in events if isinstance(e, UnitMoved)] == [1, 2]


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
    assert [e.pulse for e in events if isinstance(e, UnitMoved)] == [1, 2, 3]


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
    assert all(isinstance(e, UnitMoved) for e in events)
    pulse1 = [e for e in events if isinstance(e, UnitMoved) and e.pulse == 1]
    assert {e.unit_id for e in pulse1} == {1, 2, 3}


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


# --- Zones of control (SPEC §8.3–8.5) ---------------------------------------


def test_engaged_unit_cannot_move_zoc_to_zoc() -> None:
    # Blue infantry at (1,0) is adjacent to red at (2,0), so it begins the pulse
    # engaged. Its planned step to (1,1) — itself adjacent to red — is an illegal
    # ZOC→ZOC move (SPEC §8.4), so it stays put.
    state = build(
        ["...", "..."],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 1))])

    assert at(new_state, 1, 0) == 1  # never left its hex
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(1, 0),
            target=ax(1, 1),
            reason=MoveBlockedReason.ZOC_LOCKED,
        )
    ]


def test_engaged_unit_may_withdraw_to_a_free_hex() -> None:
    # Blue at (1,0) is engaged by red at (0,0). Stepping to (2,0) — not adjacent
    # to red, so not in its ZOC — is a legal withdrawal (SPEC §8.4).
    state = build(
        ["...."],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (2, 0))])

    assert at(new_state, 2, 0) == 1
    assert events == [UnitMoved(pulse=1, unit_id=1, origin=ax(1, 0), dest=ax(2, 0))]


def test_unengaged_unit_may_step_into_enemy_controlled_empty_hex() -> None:
    # Blue armor begins each pulse unengaged until it actually reaches red's ZOC.
    # Stepping onto (2,0) — adjacent to red at (3,0) — is allowed (it only becomes
    # engaged for the *next* pulse), and entering ZOC never causes combat.
    state = build(
        ["....."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0))])

    assert at(new_state, 2, 0) == 1  # reached the enemy-controlled hex
    assert not any(isinstance(e, CombatResolved) for e in events)
    assert [e for e in events if isinstance(e, UnitMoved)] == [
        UnitMoved(pulse=1, unit_id=1, origin=ax(0, 0), dest=ax(1, 0)),
        UnitMoved(pulse=2, unit_id=1, origin=ax(1, 0), dest=ax(2, 0)),
    ]


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
