"""Turn execution composer (SPEC §11.3; roadmap Phase 3+).

``resolve_execution`` runs the execution-sequence phases that exist so far, in
order: **Barrage (Phase 1) → Movement pulses + combat (Phase 2)**. It threads the
game state through each phase and concatenates their event logs into the single
replay for the turn.

This is the seam that later phases extend: Phase 4 appends capture, repair,
economy, and recruitment after movement, and Phase 5 wraps the whole thing as the
canonical ``resolve_turn(state, orders) -> (new_state, event_log)`` the API calls.
Like its constituents, this function is pure — it copies state, mutates nothing,
and performs no I/O.
"""

from __future__ import annotations

from app.engine.barrage import resolve_barrage
from app.engine.events import Event
from app.engine.models import GameState, Order
from app.engine.movement import resolve_movement


def resolve_execution(
    state: GameState, orders: list[Order]
) -> tuple[GameState, list[Event]]:
    """Resolve barrage then movement+combat, returning new state + the full log.

    A unit has exactly one order, so an artillery unit that barrages does not
    also move (movement ignores non-Move orders) — satisfying "barrage XOR move"
    (SPEC §5.4). Destroyed units are removed by each phase, so a unit killed by
    barrage is gone before the first movement pulse (SPEC §11.3).
    """
    after_barrage, barrage_events = resolve_barrage(state, orders)
    after_movement, movement_events = resolve_movement(after_barrage, orders)
    return after_movement, barrage_events + movement_events
