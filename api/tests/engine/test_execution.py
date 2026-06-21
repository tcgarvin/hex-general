"""Tests for the turn execution composer (roadmap Phase 3; SPEC §11.3).

`resolve_execution` runs barrage then movement+combat, proving the phase ordering:
a unit destroyed by barrage is gone before the first movement pulse, so a mover
can walk into the hex it vacated.
"""

from __future__ import annotations

from app.engine.events import BarrageResolved, UnitMoved
from app.engine.execution import resolve_execution
from app.engine.models import Order
from engine.helpers import alive, at, barrage, build, hp_of, move


def test_barrage_resolves_before_movement() -> None:
    # Blue artillery flattens the red infantry blocking the bridge hex (1,0);
    # blue infantry then advances through the now-empty hex in the same turn.
    state = build(
        ["...."],
        [
            {"type": "artillery", "owner": "blue", "q": 3, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 1},
        ],
    )
    orders: list[Order] = [barrage(1, 1, 0), move(2, (1, 0))]
    new_state, events = resolve_execution(state, orders)

    assert not alive(new_state, 3)  # barraged to death
    assert at(new_state, 1, 0) == 2  # blue infantry took the cleared hex
    # The barrage event precedes every movement event.
    kinds = [e.kind for e in events]
    assert kinds.index("barrage_resolved") < kinds.index("unit_moved")


def test_barrage_event_log_is_concatenated_with_movement() -> None:
    state = build(
        ["....."],
        [
            {"type": "artillery", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},
        ],
    )
    orders: list[Order] = [barrage(1, 4, 0), move(2, (3, 0))]
    _, events = resolve_execution(state, orders)

    barrages = [e for e in events if isinstance(e, BarrageResolved)]
    moves = [e for e in events if isinstance(e, UnitMoved)]
    assert len(barrages) == 1
    assert [m.unit_id for m in moves] == [2]


def test_units_without_orders_hold_through_execution() -> None:
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}],
    )
    new_state, events = resolve_execution(state, [])
    assert at(new_state, 1, 0) == 1
    assert hp_of(new_state, 1) == 10
    assert events == []
