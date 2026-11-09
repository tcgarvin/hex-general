"""Standing travel orders: issue, march each turn, end (WORLD_SPEC §5).

A `TravelOrder` names a destination. When it is issued, `begin_travel` plans its
route (`app.engine.routing`) and stores it as a `StandingTravel` in the state.
Every turn the unit gets no fresh order, the standing order becomes that turn's
`MarchOrder` along the rest of its route. After movement, `settle_travel` reads the
turn's events to decide each order's fate: still under way (the route is trimmed
by the hexes marched), or ended (`TravelEnded`, with a reason).

Both halves are pure and deterministic. A route that can't be planned is a caller
bug here (`EngineError`): reachability is checked at the API boundary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.engine.events import (
    ColumnStuck,
    DeployCause,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    TravelEnded,
    TravelEndReason,
    UnitDeployed,
    UnitMoved,
)
from app.engine.hex_coords import Axial
from app.engine.models import (
    GameState,
    MarchOrder,
    Order,
    PlayerId,
    Posture,
    StandingTravel,
    TravelOrder,
    UnitId,
)
from app.engine.movement import EngineError
from app.engine.routing import find_route

_CONTACT_BLOCKS = frozenset({MoveBlockedReason.PACK_INTERRUPTED})
_ENGAGED_BLOCKS = frozenset({MoveBlockedReason.ENGAGED_CANNOT_PACK})
_CONTACT_DEPLOYS = frozenset({DeployCause.CONTACT, DeployCause.BARRAGED})


@dataclass(frozen=True)
class TravelPlan:
    """The travel half of a turn's orders, ready for the rest of execution.

    ``orders`` is the submitted orders with every `TravelOrder` removed and a
    `MarchOrder` added for each unit marching under a standing order this turn.
    ``standing`` is every standing order in force for the turn, by unit.
    ``events`` records orders replaced by a fresh order. ``owners`` and
    ``origins`` remember each travelling unit's owner and start-of-turn hex, so an
    order whose unit dies mid-turn can still be reported.
    """

    orders: list[Order]
    standing: dict[UnitId, StandingTravel]
    events: list[Event]
    owners: dict[UnitId, PlayerId]
    origins: dict[UnitId, Axial]


def begin_travel(state: GameState, orders: list[Order]) -> TravelPlan:
    """Fold fresh travel orders into the standing ones and emit this turn's marches.

    A fresh order of any kind replaces a unit's standing order (an
    explicit Hold or Deploy is how a player stops a column). A fresh `TravelOrder`
    is routed from the unit's current hex. Raises `EngineError` for a travel order
    naming an unknown unit, or one whose destination can't be reached.
    """
    units = {u.id: u for u in state.units}
    standing = {o.unit_id: o for o in state.standing_orders}
    events: list[Event] = []
    fresh_ids = {o.unit_id for o in orders}
    for unit_id in sorted(fresh_ids & standing.keys()):
        old = standing.pop(unit_id)
        unit = units[unit_id]
        events.append(
            TravelEnded(
                unit_id=unit_id,
                owner=unit.owner,
                coord=unit.coord,
                destination=old.destination,
                reason=TravelEndReason.REPLACED,
            )
        )

    kept: list[Order] = []
    for order in orders:
        if not isinstance(order, TravelOrder):
            kept.append(order)
            continue
        if order.unit_id not in units:
            raise EngineError(f"travel order references unknown unit {order.unit_id}")
        unit = units[order.unit_id]
        route = find_route(state.map, unit.coord, order.destination, order.via)
        if not route:
            raise EngineError(
                f"unit {unit.id} has no route from {unit.coord} to {order.destination}"
            )
        standing[unit.id] = StandingTravel(
            unit_id=unit.id,
            destination=order.destination,
            route=route,
            on_arrival=order.on_arrival,
        )

    marches: list[Order] = [
        MarchOrder(
            unit_id=travel.unit_id,
            path=list(travel.route),
            deploy_on_arrival=travel.on_arrival is Posture.DEPLOYED,
        )
        for _, travel in sorted(standing.items())
    ]
    return TravelPlan(
        orders=kept + marches,
        standing=standing,
        events=events,
        owners={uid: units[uid].owner for uid in standing},
        origins={uid: units[uid].coord for uid in standing},
    )


def settle_travel(
    plan: TravelPlan, state_after: GameState, turn_events: list[Event]
) -> tuple[list[StandingTravel], list[Event]]:
    """Decide each standing order's fate from the turn's events.

    ``state_after`` is the state once movement has resolved. Returns the orders
    still under way (routes trimmed, sorted by unit id) and a `TravelEnded` for
    each order that finished. An order ends on arrival, on contact (auto-deploy,
    a column stuck without room, or packing interrupted), when its unit began the
    turn engaged and could not pack, or when its unit died.
    """
    survivors = {u.id: u for u in state_after.units}
    steps: Counter[UnitId] = Counter()
    halted: dict[UnitId, TravelEndReason] = {}
    for event in turn_events:
        if isinstance(event, UnitMoved) and event.unit_id in plan.standing:
            steps[event.unit_id] += 1
            continue
        halt = _halt(event)
        if halt is not None and halt[0] in plan.standing:
            halted.setdefault(*halt)

    ongoing: list[StandingTravel] = []
    ended: list[Event] = []
    for unit_id, travel in sorted(plan.standing.items()):
        marched = steps[unit_id]
        remaining = travel.route[marched:]
        if unit_id not in survivors:
            reason: TravelEndReason | None = TravelEndReason.DESTROYED
        elif not remaining:
            reason = TravelEndReason.ARRIVED
        else:
            reason = halted.get(unit_id)
        if reason is None:
            ongoing.append(travel.model_copy(update={"route": remaining}))
            continue
        if unit_id in survivors:
            coord = survivors[unit_id].coord
        else:
            coord = travel.route[marched - 1] if marched else plan.origins[unit_id]
        ended.append(
            TravelEnded(
                unit_id=unit_id,
                owner=plan.owners[unit_id],
                coord=coord,
                destination=travel.destination,
                reason=reason,
            )
        )
    return ongoing, ended


def _halt(event: Event) -> tuple[UnitId, TravelEndReason] | None:
    """The unit an event halts and why, if the event ends a march early."""
    if isinstance(event, UnitDeployed) and event.cause in _CONTACT_DEPLOYS:
        return event.unit_id, TravelEndReason.CONTACT
    if isinstance(event, ColumnStuck):
        return event.unit_id, TravelEndReason.CONTACT
    if isinstance(event, MoveBlocked):
        if event.reason in _CONTACT_BLOCKS:
            return event.unit_id, TravelEndReason.CONTACT
        if event.reason in _ENGAGED_BLOCKS:
            return event.unit_id, TravelEndReason.ENGAGED
    return None
