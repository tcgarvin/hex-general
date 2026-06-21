"""Tests for the barrage phase (roadmap Phase 3; SPEC §7.2, §11.3 Phase 1).

Barrage targets a hex and damages its occupant before any movement, with terrain
and Hold modifiers but no retaliation (artillery fires at range). Out-of-range or
non-artillery orders violate the engine's contract and raise `EngineError`.
"""

from __future__ import annotations

import pytest

from app.engine.barrage import resolve_barrage
from app.engine.events import BarrageResolved
from app.engine.models import Order
from app.engine.movement import EngineError
from engine.helpers import alive, at, ax, barrage, build, hold, hp_of, move


def test_barrage_damages_occupant_without_retaliation() -> None:
    # Artillery (atk 5) hits plains infantry (def 5): clamp(5 - 5) = 1 damage.
    state = build(
        ["....."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
        ],
    )
    new_state, events = resolve_barrage(state, [barrage(1, 3, 0)])

    assert hp_of(new_state, 2) == 9
    assert hp_of(new_state, 1) == 10  # artillery is untouched
    (event,) = [e for e in events if isinstance(e, BarrageResolved)]
    assert event.hit is not None
    assert event.hit.unit_id == 2 and event.hit.damage == 1


def test_barrage_applies_terrain_and_hold_modifiers() -> None:
    # Woods (+2) plus Hold (+1) reduce artillery's hit: clamp(5 - 5 - 3) = 1.
    state = build(
        ["...w"],  # (3,0) woods
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0, "hp": 10},
        ],
    )
    orders: list[Order] = [barrage(1, 3, 0), hold(2)]
    new_state, events = resolve_barrage(state, orders)

    assert hp_of(new_state, 2) == 9
    (event,) = [e for e in events if isinstance(e, BarrageResolved)]
    assert event.hit is not None
    assert event.hit.terrain_bonus == 2
    assert event.hit.hold_bonus == 1


def test_barrage_destroys_low_hp_occupant() -> None:
    state = build(
        ["...."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "armor", "owner": "red", "q": 3, "r": 0, "hp": 1},
        ],
    )
    new_state, events = resolve_barrage(state, [barrage(1, 3, 0)])

    assert not alive(new_state, 2)
    (event,) = [e for e in events if isinstance(e, BarrageResolved)]
    assert event.hit is not None and event.hit.destroyed is True


def test_barrage_on_empty_hex_is_a_no_op_hit() -> None:
    state = build(
        ["...."],
        [{"type": "artillery", "owner": "blue", "q": 0, "r": 0}],
    )
    new_state, events = resolve_barrage(state, [barrage(1, 2, 0)])

    assert [u.id for u in new_state.units] == [1]
    assert events == [BarrageResolved(artillery_id=1, target=ax(2, 0), hit=None)]


def test_barrage_hits_a_friendly_occupant() -> None:
    # Barrage targets a hex, so it can land on a friendly unit (SPEC §7.2).
    state = build(
        ["...."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 3, "r": 0},
        ],
    )
    new_state, _ = resolve_barrage(state, [barrage(1, 3, 0)])
    assert hp_of(new_state, 2) == 9


def test_two_barrages_on_one_hex_are_simultaneous() -> None:
    # Both artillery target the same infantry; damage sums before removal.
    state = build(
        [".....", "....."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "artillery", "owner": "blue", "q": 0, "r": 1},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0, "hp": 1},
        ],
    )
    new_state, events = resolve_barrage(state, [barrage(1, 2, 0), barrage(2, 2, 0)])

    assert not alive(new_state, 3)
    hits = [e for e in events if isinstance(e, BarrageResolved)]
    assert len(hits) == 2
    assert all(h.hit is not None and h.hit.destroyed for h in hits)


def test_barrage_out_of_range_raises() -> None:
    state = build(
        ["....."],  # artillery range is 3; target at distance 4
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},
        ],
    )
    with pytest.raises(EngineError, match="out of range"):
        resolve_barrage(state, [barrage(1, 4, 0)])


def test_non_artillery_barrage_raises() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
    )
    with pytest.raises(EngineError, match="cannot barrage"):
        resolve_barrage(state, [barrage(1, 1, 0)])


def test_barrage_ignores_move_orders() -> None:
    # resolve_barrage only touches Barrage orders; a Move order is left for the
    # movement phase.
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
    )
    new_state, events = resolve_barrage(state, [move(1, (1, 0))])
    assert events == []
    assert at(new_state, 0, 0) == 1
