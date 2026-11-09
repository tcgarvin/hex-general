"""Standing travel orders across turns (WORLD_SPEC §5).

A Travel order is routed once, stored in `GameState.standing_orders`, and marched
every turn the unit has no fresh order, until it arrives, meets the enemy, is
replaced, or its unit dies. Turn speeds come from `COLUMN_RULES` (12 road hexes on
the packing turn, 16 a turn after).
"""

from __future__ import annotations

from typing import Any

from app.engine.events import (
    OrderMooted,
    TravelEnded,
    TravelEndReason,
    UnitDeployed,
)
from app.engine.execution import resolve_execution
from app.engine.fog import filter_events, redact_state
from app.engine.models import (
    DeployOrder,
    GameState,
    Order,
    Posture,
    StandingTravel,
    TravelOrder,
    Unit,
)
from app.engine.routing import find_route, travel_eta

from engine.helpers import ax, barrage, build, hold
from engine.test_routing import MIXED_ROADS

LONG = "." * 70
LONG_ROAD = "#" * 70


def unit(state: GameState, unit_id: int) -> Unit:
    return next(u for u in state.units if u.id == unit_id)


def ended(events: list[Any]) -> list[TravelEnded]:
    return [e for e in events if isinstance(e, TravelEnded)]


def road_world(*extra: dict[str, Any]) -> GameState:
    units = [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}, *extra]
    players = sorted({u["owner"] for u in units} | {"red"})
    return build([LONG], units, roads=[LONG_ROAD], players=players)


def run(
    state: GameState, first: list[Order], turns: int
) -> tuple[GameState, list[list[Any]]]:
    """Resolve ``turns`` turns: ``first`` on the first, no orders after."""
    log: list[list[Any]] = []
    orders = first
    for _ in range(turns):
        state, events = resolve_execution(state, orders)
        log.append(events)
        orders = []
    return state, log


def test_travel_marches_every_turn_until_it_arrives_and_deploys() -> None:
    state = road_world()
    order = TravelOrder(unit_id=1, destination=ax(50, 0))

    state, log = run(state, [order], 1)
    assert unit(state, 1).coord == ax(12, 0)
    assert unit(state, 1).posture is Posture.COLUMN
    assert state.standing_orders == [
        StandingTravel(
            unit_id=1,
            destination=ax(50, 0),
            route=[ax(q, 0) for q in range(13, 51)],
        )
    ]

    state, log = run(state, [], 2)
    assert unit(state, 1).coord == ax(44, 0)
    assert not ended(log[0] + log[1])

    state, log = run(state, [], 1)
    assert unit(state, 1).coord == ax(50, 0)
    assert unit(state, 1).posture is Posture.DEPLOYED
    assert not unit(state, 1).disordered
    assert state.standing_orders == []
    assert ended(log[0]) == [
        TravelEnded(
            unit_id=1,
            owner="blue",
            coord=ax(50, 0),
            destination=ax(50, 0),
            reason=TravelEndReason.ARRIVED,
        )
    ]


def test_travel_can_arrive_still_packed() -> None:
    state = road_world()
    order = TravelOrder(unit_id=1, destination=ax(20, 0), on_arrival=Posture.COLUMN)
    state, log = run(state, [order], 2)
    assert unit(state, 1).coord == ax(20, 0)
    assert unit(state, 1).posture is Posture.COLUMN
    assert [e.reason for e in ended(log[1])] == [TravelEndReason.ARRIVED]


def test_arrival_takes_exactly_the_eta() -> None:
    state = build(
        ["." * len(MIXED_ROADS)],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        roads=[MIXED_ROADS],
    )
    destination = ax(17, 0)
    route = find_route(state.map, ax(0, 0), destination)
    assert route is not None
    eta = travel_eta(state.map, route, packed=False)

    state, log = run(state, [TravelOrder(unit_id=1, destination=destination)], eta)
    assert unit(state, 1).coord == destination
    assert [e.reason for e in ended(log[-1])] == [TravelEndReason.ARRIVED]
    assert not ended([e for events in log[:-1] for e in events])


def test_contact_halts_travel_and_ends_the_order() -> None:
    state = road_world({"type": "infantry", "owner": "red", "q": 40, "r": 0})
    order = TravelOrder(unit_id=1, destination=ax(60, 0))
    state, log = run(state, [order, hold(2)], 3)

    blue = unit(state, 1)
    assert blue.coord == ax(36, 0)  # stopped 4 hexes short: the next step is in X
    assert blue.posture is Posture.DEPLOYED
    assert state.standing_orders == []
    halts = [e for events in log for e in ended(events)]
    assert [e.reason for e in halts] == [TravelEndReason.CONTACT]
    deploys = [e for events in log for e in events if isinstance(e, UnitDeployed)]
    assert deploys and deploys[0].disordered


def test_a_fresh_order_replaces_the_standing_one() -> None:
    state = road_world()
    state, _ = run(state, [TravelOrder(unit_id=1, destination=ax(60, 0))], 1)

    state, log = run(state, [hold(1)], 2)
    assert unit(state, 1).coord == ax(12, 0)  # held, then stayed put with no order
    assert state.standing_orders == []
    assert [e.reason for e in ended(log[0])] == [TravelEndReason.REPLACED]
    assert not ended(log[1])


def test_deploy_order_stops_a_travelling_column() -> None:
    state = road_world()
    state, _ = run(state, [TravelOrder(unit_id=1, destination=ax(60, 0))], 1)
    state, log = run(state, [DeployOrder(unit_id=1)], 1)
    assert unit(state, 1).posture is Posture.DEPLOYED
    assert unit(state, 1).coord == ax(12, 0)
    assert [e.reason for e in ended(log[0])] == [TravelEndReason.REPLACED]


def test_new_travel_order_reroutes() -> None:
    state = road_world()
    state, _ = run(state, [TravelOrder(unit_id=1, destination=ax(60, 0))], 1)
    state, log = run(state, [TravelOrder(unit_id=1, destination=ax(2, 0))], 1)
    assert unit(state, 1).coord == ax(2, 0)
    assert [e.reason for e in ended(log[0])] == [
        TravelEndReason.REPLACED,
        TravelEndReason.ARRIVED,
    ]


def test_engaged_unit_cannot_start_travel() -> None:
    state = road_world({"type": "infantry", "owner": "red", "q": 1, "r": 0})
    order = TravelOrder(unit_id=1, destination=ax(30, 0))
    state, log = run(state, [order, hold(2)], 1)
    assert unit(state, 1).coord == ax(0, 0)
    assert unit(state, 1).posture is Posture.DEPLOYED
    assert state.standing_orders == []
    assert [e.reason for e in ended(log[0])] == [TravelEndReason.ENGAGED]


def test_destroyed_traveller_ends_its_order_where_it_fell() -> None:
    state = road_world({"type": "artillery", "owner": "red", "q": 30, "r": 0})
    blue = unit(state, 1)
    blue.coord, blue.posture, blue.hp = ax(27, 0), Posture.COLUMN, 1
    state.standing_orders = [
        StandingTravel(
            unit_id=1, destination=ax(40, 0), route=[ax(q, 0) for q in range(28, 41)]
        )
    ]
    state, log = run(state, [barrage(2, 27, 0)], 1)
    assert all(u.id != 1 for u in state.units)
    assert state.standing_orders == []
    assert [(e.reason, e.coord) for e in ended(log[0])] == [
        (TravelEndReason.DESTROYED, ax(27, 0))
    ]
    assert any(isinstance(e, OrderMooted) for e in log[0])


def test_standing_orders_are_private_to_their_owner() -> None:
    state = road_world({"type": "infantry", "owner": "red", "q": 69, "r": 0})
    state, log = run(state, [TravelOrder(unit_id=1, destination=ax(30, 0))], 1)
    assert redact_state(state, "red").standing_orders == []
    assert len(redact_state(state, "blue").standing_orders) == 1

    state, log = run(state, [], 2)
    assert ended(filter_events(log[1], state, "blue"))
    assert not ended(filter_events(log[1], state, "red"))


def test_standing_orders_round_trip_through_json() -> None:
    state = road_world()
    state, _ = run(state, [TravelOrder(unit_id=1, destination=ax(50, 0))], 1)
    restored = GameState.model_validate_json(state.model_dump_json())
    assert restored.model_dump() == state.model_dump()
    assert restored.standing_orders == state.standing_orders


def test_travel_is_deterministic() -> None:
    def once() -> list[Any]:
        state = road_world({"type": "infantry", "owner": "red", "q": 45, "r": 0})
        _, log = run(state, [TravelOrder(unit_id=1, destination=ax(60, 0))], 4)
        return log

    assert once() == once()
