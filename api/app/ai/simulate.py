"""Turn simulation wrapper used by the arena and the search planner.

`resolve_plan` is `resolve_execution` with one boundary guard: the barrage phase
(SPEC §11.3 Phase 1) runs *before* movement and removes destroyed units, and
`resolve_movement` validates that **every** order references a live unit — so an
order for a unit the barrage kills makes the engine raise ``EngineError`` (it
cannot tell a moot order from a bad one). That state is reachable in real
simultaneous play (an enemy barrages a unit you ordered to move) and inside the
search (our barrage kills an enemy the opponent-model ordered to Hold), so this
wrapper drops the now-moot orders for barrage-killed units first.

It does not modify the engine and does not change any surviving unit's outcome:
the dropped orders belong only to units that are gone before movement begins. The
pre-barrage pass runs only when the plan actually contains a Barrage order.
"""

from __future__ import annotations

from app.engine.barrage import resolve_barrage
from app.engine.events import Event
from app.engine.execution import resolve_execution
from app.engine.models import BarrageOrder, GameState, Order, Recruitment


def resolve_plan(
    state: GameState,
    orders: list[Order],
    recruitments: list[Recruitment] | None = None,
) -> tuple[GameState, list[Event]]:
    """Resolve a full turn, dropping orders for units the barrage destroys."""
    recruitments = recruitments if recruitments is not None else []
    if any(isinstance(order, BarrageOrder) for order in orders):
        after_barrage, _ = resolve_barrage(state, orders)
        live = {u.id for u in after_barrage.units}
        orders = [order for order in orders if order.unit_id in live]
    return resolve_execution(state, orders, recruitments)
