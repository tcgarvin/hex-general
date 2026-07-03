"""Tests for the turn execution composer (roadmap Phase 3; SPEC §11.3).

`resolve_execution` runs barrage then movement+combat, proving the phase ordering:
a unit destroyed by barrage is gone before the first movement pulse, so a mover
can walk into the hex it vacated.
"""

from __future__ import annotations

import pytest

from app.engine.events import (
    BarrageResolved,
    OrderMooted,
    OrderMootedReason,
    SettlementCaptured,
    UnitMoved,
)
from app.engine.execution import resolve_execution
from app.engine.models import GameState, Order
from app.engine.movement import EngineError
from app.engine.victory import VictoryReason, victory_status
from engine.helpers import alive, at, barrage, build, hold, hp_of, move, rest


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
    # No movement/combat happened; the only events are the end-of-turn economy
    # accounting (one per player), and the turn advanced.
    assert {e.kind for e in events} == {"economy_resolved"}
    assert new_state.turn == state.turn + 1


def test_order_for_barrage_killed_unit_is_mooted_not_crashed() -> None:
    # Blue artillery flattens the red infantry, which was itself ordered to Hold.
    # Barrage removes it before movement, so its Hold order is moot: the engine
    # drops it and logs OrderMooted rather than crashing (SPEC §11.3).
    state = build(
        ["...."],
        [
            {"type": "artillery", "owner": "blue", "q": 3, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 1},
        ],
    )
    orders: list[Order] = [barrage(1, 1, 0), hold(2)]
    new_state, events = resolve_execution(state, orders)

    assert not alive(new_state, 2)  # barraged to death
    mooted = [e for e in events if isinstance(e, OrderMooted)]
    assert len(mooted) == 1
    assert mooted[0].unit_id == 2
    assert mooted[0].owner == "red"  # the dead unit's owner, for fog filtering
    assert mooted[0].reason is OrderMootedReason.DESTROYED_IN_BARRAGE
    # The mooted event sits between the barrage events and the movement events.
    kinds = [e.kind for e in events]
    assert kinds.index("barrage_resolved") < kinds.index("order_mooted")


def test_move_and_rest_orders_for_barrage_killed_units_are_mooted() -> None:
    # Two red units, one ordered to Move and one to Rest, are both barraged dead.
    # Neither order applies; both are mooted and resolution completes.
    state = build(
        [".....", "....."],
        [
            {"type": "artillery", "owner": "blue", "q": 4, "r": 0},
            {"type": "artillery", "owner": "blue", "q": 4, "r": 1},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 1},
            {"type": "infantry", "owner": "red", "q": 1, "r": 1, "hp": 1},
        ],
    )
    orders: list[Order] = [
        barrage(1, 1, 0),
        barrage(2, 1, 1),
        move(3, (2, 0)),
        rest(4),
    ]
    new_state, events = resolve_execution(state, orders)

    assert not alive(new_state, 3)
    assert not alive(new_state, 4)
    mooted = {e.unit_id for e in events if isinstance(e, OrderMooted)}
    assert mooted == {3, 4}
    assert not [e for e in events if isinstance(e, UnitMoved)]  # nobody moved


def test_mutual_barrage_kill_moots_nothing() -> None:
    # Two artillery shell each other; barrage is simultaneous, so both shots
    # resolve even though both firers die (SPEC §7.2, §11.3 Phase 1). Their
    # Barrage orders already applied — nothing is mooted, nothing crashes.
    state = build(
        ["..."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0, "hp": 1},
            {"type": "artillery", "owner": "red", "q": 2, "r": 0, "hp": 1},
        ],
    )
    orders: list[Order] = [barrage(1, 2, 0), barrage(2, 0, 0)]
    new_state, events = resolve_execution(state, orders)

    assert not alive(new_state, 1)
    assert not alive(new_state, 2)
    barrages = [e for e in events if isinstance(e, BarrageResolved)]
    assert len(barrages) == 2  # both shots resolved despite both firers dying
    assert all(b.hit is not None and b.hit.destroyed for b in barrages)
    assert not [e for e in events if isinstance(e, OrderMooted)]


def test_order_for_never_existed_unit_still_raises() -> None:
    # An order naming a unit id that never existed is a caller bug: the barrage
    # phase ignores it (not a BarrageOrder), and movement raises EngineError.
    state = build(
        ["..."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
    )
    orders: list[Order] = [move(99, (1, 0))]
    with pytest.raises(EngineError):
        resolve_execution(state, orders)


def _march_order(state: GameState, unit_id: int, target_q: int) -> Order:
    """Plot the remaining straight-line steps for ``unit_id`` toward ``target_q``."""
    unit = next(u for u in state.units if u.id == unit_id)
    steps = [(q, 0) for q in range(unit.coord.q + 1, target_q + 1)]
    return move(unit_id, *steps)


def test_headless_game_drives_to_city_elimination_victory() -> None:
    # Milestone (roadmap P4): the engine drives a full game start→victory with no
    # UI. Blue armor marches across an undefended corridor into red's lone city;
    # capturing it leaves red with zero cities, so blue wins by elimination.
    state = build(
        ["c.....c"],
        [{"type": "armor", "owner": "blue", "q": 1, "r": 0}],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 6, "r": 0, "owner": "red"},
        ],
        players=["blue", "red"],
    )

    status = victory_status(state)
    assert status.reason is VictoryReason.ONGOING

    captures: list[SettlementCaptured] = []
    turns = 0
    while not status.decided and turns < 10:
        state, events = resolve_execution(state, [_march_order(state, 1, 6)])
        captures += [e for e in events if isinstance(e, SettlementCaptured)]
        status = victory_status(state)
        turns += 1

    assert status.reason is VictoryReason.CITY_ELIMINATION
    assert status.winner == "blue"
    assert turns == 2  # armor (move 4) needs two turns to cross five hexes
    assert state.turn == 2  # the engine advanced the turn each resolution
    assert any(c.coord.q == 6 and c.captured_by == "blue" for c in captures)
