"""Turn execution composer (SPEC §11.3; roadmap Phase 3–4).

``resolve_execution`` runs the full turn-resolution sequence in spec order and
threads the game state through each phase, concatenating their event logs into the
single replay for the turn:

  1. **Barrage** (§11.3 Phase 1) — pre-movement artillery fire.
  2. **Movement + combat** (§11.3 Phase 2) — movement pulses with interleaved
     combat.
  3. **Capture** (§11.3 Phase 3, §14) — settlements flip to occupying infantry/armor.
  4. **Repair** (§11.3 Phase 4, §7.3) — undamaged resting units recover HP.
  5. **Economy** (§11.3 Phase 5, §6.2–6.4) — income and upkeep update each bank.
  6. **Recruitment** (§11.3 Phase 6, §6.5) — banked muster deploys new units.

It then advances ``turn`` by one: the returned state is the *next* turn's planning
state, so a driver can loop ``state, _ = resolve_execution(state, …)`` and reach
the turn limit. Whether the game is over is a separate query (`victory_status`).

Phase 5 wraps this as the canonical ``resolve_turn`` the API calls, adding identity
and persistence. Like its constituents, this function is pure — it copies state,
mutates nothing, and performs no I/O.
"""

from __future__ import annotations

from app.engine.barrage import resolve_barrage
from app.engine.capture import resolve_capture
from app.engine.economy import resolve_economy
from app.engine.events import Event
from app.engine.models import GameState, Order, Recruitment
from app.engine.movement import resolve_movement
from app.engine.recruitment import resolve_recruitment
from app.engine.repair import resolve_repair


def resolve_execution(
    state: GameState,
    orders: list[Order],
    recruitments: list[Recruitment] | None = None,
) -> tuple[GameState, list[Event]]:
    """Resolve a full turn, returning the next-turn state and the full event log.

    A unit has exactly one order, so an artillery unit that barrages does not also
    move (movement ignores non-Move orders) — satisfying "barrage XOR move"
    (SPEC §5.4). Destroyed units are removed by each phase, so a unit killed by
    barrage is gone before the first movement pulse (SPEC §11.3). ``recruitments``
    defaults to none; an illegal recruit raises `EngineError` (validated in
    `resolve_recruitment`).
    """
    recruitments = recruitments if recruitments is not None else []
    pre_hp = {u.id: u.hp for u in state.units}

    after_barrage, barrage_events = resolve_barrage(state, orders)
    after_movement, movement_events = resolve_movement(after_barrage, orders)

    # A resting unit that lost HP during barrage/movement does not repair (§7.3).
    damaged = {u.id for u in after_movement.units if u.hp < pre_hp[u.id]}

    after_capture, capture_events = resolve_capture(after_movement)
    after_repair, repair_events = resolve_repair(after_capture, orders, damaged)
    after_economy, economy_events = resolve_economy(after_repair)
    after_recruit, recruit_events = resolve_recruitment(after_economy, recruitments)

    next_state = after_recruit.model_copy(update={"turn": after_recruit.turn + 1})
    events = (
        barrage_events
        + movement_events
        + capture_events
        + repair_events
        + economy_events
        + recruit_events
    )
    return next_state, events
