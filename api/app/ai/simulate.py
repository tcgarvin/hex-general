"""Turn resolution wrapper used by the arena and the search planner.

`resolve_plan` runs the engine's exact phase sequence (`resolve_execution`'s
composition, SPEC §11.3) with one added boundary responsibility: **dropping
intents that the turn's own simultaneous dynamics have made moot**, which the
engine — by design — treats as caller bugs and crashes on:

* *Orders for barrage-killed units.* The barrage phase removes destroyed units
  before movement, and `resolve_movement` raises for an order whose unit no
  longer exists. An enemy barrage killing a unit you ordered to move is normal
  simultaneous play (and, inside the search, our own barrage kills enemies the
  §4.4 opponent model ordered to Hold), so those orders are dropped, not bugs.
* *Recruitments invalidated mid-turn.* Recruitment resolves last, after
  movement/capture/economy, so a statically-legal recruit can be voided by the
  time it applies: the city was captured by a (possibly fog-hidden) enemy, a
  unit ended its move on the hex, or the post-economy bank fell short. The
  muster simply stays banked — the moot request is dropped before the phase.

Both drops mirror what simultaneous resolution *means* for stale intents; they
never change a surviving unit's outcome and add nothing the engine would not
otherwise reject. The engine itself is not modified (AI_SPEC §3).
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
from app.engine.stats import UNIT_STATS


def _viable_recruitments(
    state: GameState, recruitments: list[Recruitment]
) -> list[Recruitment]:
    """The recruitments still applicable against the pre-recruitment state.

    Mirrors `resolve_recruitment`'s *dynamic* checks (control, occupancy, cap,
    running bank) and drops requests the turn has voided. Static properties
    (the hex is a city, the owner exists) were guaranteed by submission-time
    validation and are left to the engine — failing them there is a real bug.
    """
    if not recruitments:
        return recruitments
    control = {s.coord: s.controlled_by for s in state.settlements}
    occupied = {u.coord for u in state.units}
    bank = {p.id: p.bank for p in state.players}
    counts: dict[str, int] = {p.id: 0 for p in state.players}
    for unit in state.units:
        counts[unit.owner] += 1
    cap = state.config.unit_cap

    kept: list[Recruitment] = []
    for request in recruitments:
        cost = UNIT_STATS[request.type].recruit_cost
        if (
            control.get(request.coord) == request.owner
            and request.coord not in occupied
            and counts[request.owner] < cap
            and bank[request.owner] >= cost
        ):
            kept.append(request)
            bank[request.owner] -= cost
            counts[request.owner] += 1
            occupied.add(request.coord)
    return kept


def resolve_plan(
    state: GameState,
    orders: list[Order],
    recruitments: list[Recruitment] | None = None,
) -> tuple[GameState, list[Event]]:
    """Resolve a full turn, dropping intents mooted by simultaneous dynamics."""
    recruitments = recruitments if recruitments is not None else []
    pre_hp = {u.id: u.hp for u in state.units}

    after_barrage, barrage_events = resolve_barrage(state, orders)
    live = {u.id for u in after_barrage.units}
    orders = [order for order in orders if order.unit_id in live]
    after_movement, movement_events = resolve_movement(after_barrage, orders)

    # A resting unit that lost HP during barrage/movement does not repair (§7.3).
    damaged = {u.id for u in after_movement.units if u.hp < pre_hp[u.id]}

    after_capture, capture_events = resolve_capture(after_movement)
    after_repair, repair_events = resolve_repair(after_capture, orders, damaged)
    after_economy, economy_events = resolve_economy(after_repair)
    after_recruit, recruit_events = resolve_recruitment(
        after_economy, _viable_recruitments(after_economy, recruitments)
    )

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
