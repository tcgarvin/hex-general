"""Turn execution composer (SPEC §11.3; roadmap Phase 3–4).

``resolve_execution`` runs the full turn-resolution sequence in spec order and
threads the game state through each phase, concatenating their event logs into the
single replay for the turn:

  1. **Barrage** (§11.3 Phase 1) — pre-movement artillery fire.
  2. **Movement + combat** (§11.3 Phase 2) — movement pulses with interleaved
     combat. Standing travel orders (WORLD_SPEC §5) march as part of it: they
     are expanded into March orders first and settled (trimmed or ended) after.
  3. **Capture** (§11.3 Phase 3, §14) — settlements flip to occupying infantry/armor.
  4. **Repair** (§11.3 Phase 4, §7.3) — undamaged resting units recover HP.
  5. **Economy** (§11.3 Phase 5, §6.2–6.4) — income and upkeep update each bank.
     A world game runs `resolve_world_economy` instead: three resources,
     shortages that disband units, and base construction.
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
from app.engine.events import BarrageResolved, Event, OrderMooted, OrderMootedReason
from app.engine.models import (
    NEUTRAL,
    BarrageOrder,
    EconomyOrders,
    GameState,
    HoldOrder,
    Order,
    Recruitment,
)
from app.engine.movement import EngineError, resolve_movement
from app.engine.recruitment import resolve_recruitment
from app.engine.repair import resolve_repair
from app.engine.travel import begin_travel, settle_travel
from app.engine.world_economy import resolve_world_economy


def resolve_execution(
    state: GameState,
    orders: list[Order],
    recruitments: list[Recruitment] | None = None,
    economy_orders: list[EconomyOrders] | None = None,
) -> tuple[GameState, list[Event]]:
    """Resolve a full turn, returning the next-turn state and the full event log.

    A unit has exactly one order, so an artillery unit that barrages does not also
    move (movement ignores non-Move orders) — satisfying "barrage XOR move"
    (SPEC §5.4). Destroyed units are removed by each phase, so a unit killed by
    barrage is gone before the first movement pulse (SPEC §11.3).

    Because barrage (Phase 1) removes destroyed units before movement (Phase 2), a
    statically-legal Move/Hold/Rest order can name a unit that died this turn. The
    engine treats that as defined simultaneity: the order is dropped and an
    ``OrderMooted`` event is logged (between the barrage and movement events). A
    Barrage order whose artillery died in the exchange is dropped *silently* —
    barrage resolves simultaneously, so a dead artillery still fired and its order
    already applied in Phase 1; nothing remains to moot. An order naming a unit
    that *never* existed is still a caller bug and falls through to
    ``resolve_movement``, which raises `EngineError`. ``recruitments`` defaults to
    none; a request voided by the turn is skipped with a ``RecruitmentVoided``
    event, while a static violation (unknown player, non-city hex) still raises
    (see ``resolve_recruitment``).
    """
    recruitments = recruitments if recruitments is not None else []
    economy_orders = economy_orders if economy_orders is not None else []
    if economy_orders and state.config.economy is None:
        raise EngineError("economy orders need a world economy")
    orders = orders + _garrison_holds(state, orders)
    # Travel (WORLD_SPEC §5): fresh Travel orders become standing ones, and every
    # standing order with no fresh order to replace it marches this turn.
    travel = begin_travel(state, orders)
    orders = travel.orders
    pre_hp = {u.id: u.hp for u in state.units}
    # Disorder lasts through the end of the turn after the one it was earned in
    # (WORLD_SPEC §4.5): clear it now only for units that began this turn with it.
    pre_disordered = {u.id for u in state.units if u.disordered}
    pre_barrage_owner = {u.id: u.owner for u in state.units}

    after_barrage, barrage_events = resolve_barrage(state, orders)

    live_ids = {u.id for u in after_barrage.units}
    live_orders: list[Order] = []
    mooted_events: list[Event] = []
    for order in orders:
        if order.unit_id in live_ids or order.unit_id not in pre_barrage_owner:
            # Alive → keep. Never existed → keep so movement raises EngineError.
            live_orders.append(order)
        elif isinstance(order, BarrageOrder):
            # The artillery died in the simultaneous barrage exchange, but its own
            # shot still resolved in Phase 1 (BarrageResolved is already logged).
            # Nothing remains for later phases — drop silently, not mooted.
            continue
        else:
            # Existed pre-barrage but is now dead: the barrage mooted this order.
            mooted_events.append(
                OrderMooted(
                    unit_id=order.unit_id,
                    owner=pre_barrage_owner[order.unit_id],
                    reason=OrderMootedReason.DESTROYED_IN_BARRAGE,
                )
            )

    barraged = frozenset(
        hit.unit_id
        for event in barrage_events
        if isinstance(event, BarrageResolved)
        for hit in ([event.hit] if event.hit is not None else []) + event.extra_hits
        if not hit.destroyed
    )
    after_movement, movement_events = resolve_movement(
        after_barrage, live_orders, barraged=barraged
    )

    standing, travel_events = settle_travel(
        travel, after_movement, barrage_events + movement_events
    )
    after_movement = after_movement.model_copy(update={"standing_orders": standing})

    # A resting unit that lost HP during barrage/movement does not repair (§7.3).
    damaged = {u.id for u in after_movement.units if u.hp < pre_hp[u.id]}

    after_capture, capture_events = resolve_capture(after_movement)
    after_repair, repair_events = resolve_repair(after_capture, live_orders, damaged)
    if after_repair.config.economy is None:
        after_economy, economy_events = resolve_economy(after_repair)
    else:
        after_economy, economy_events = resolve_world_economy(
            after_repair, economy_orders
        )
    after_recruit, recruit_events = resolve_recruitment(after_economy, recruitments)

    next_state = after_recruit.model_copy(update={"turn": after_recruit.turn + 1})
    for unit in next_state.units:
        if unit.id in pre_disordered:
            unit.disordered = False
    events = (
        barrage_events
        + mooted_events
        + movement_events
        + travel.events
        + travel_events
        + capture_events
        + repair_events
        + economy_events
        + recruit_events
    )
    return next_state, events


def _garrison_holds(state: GameState, orders: list[Order]) -> list[Order]:
    """Hold orders for every neutral garrison unit: garrisons always hold.

    Raises `EngineError` if a submitted order names a garrison unit, since no
    player commands the neutral side.
    """
    garrison = {u.id for u in state.units if u.owner == NEUTRAL}
    if not garrison:
        return []
    ordered = {o.unit_id for o in orders}
    if ordered & garrison:
        raise EngineError(
            f"orders name neutral garrison units {sorted(ordered & garrison)}"
        )
    return [HoldOrder(unit_id=uid) for uid in sorted(garrison)]
