"""World bot operations: when a bot goes to war, and how its force fights.

Player ``p`` (the bot under test) holds its base and a front farm; player ``q``
holds a farm across the border and its own base. The strategist tests call
`plan_operation` directly; the tactical tests hand `plan_world_turn` an
`Operation` and check the shape of the orders.
"""

from __future__ import annotations

from app.ai.operations import Operation, plan_operation
from app.ai.world import (
    Personality,
    ScriptedStrategist,
    Stance,
    StrategistArgs,
    plan_world_turn,
)
from app.engine.economy_rules import SiteKind, Stocks
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, distance, neighbors, within
from app.engine.models import GameState, MoveOrder, Order, TravelOrder, Unit
from app.games.validation import validate_submission
from ai.world_fixtures import INF, at, make_unit, small_world

BASE = at(1, 1)
FRONT = at(7, 6)  # p's farm at the border
Q_FARM = at(12, 6)  # q's farm across it
Q_BASE = at(14, 10)
BOLD = 0.9
CAUTIOUS = 0.2


def _world(units: list[Unit], extra: dict[Axial, str] | None = None) -> GameState:
    held = {BASE: "p", FRONT: "p", Q_FARM: "q", Q_BASE: "q", **(extra or {})}
    sites = {
        c: (SiteKind.BASE if c in (BASE, Q_BASE) else SiteKind.FARM, 1) for c in held
    }
    return small_world(
        sites, units, held=held, width=18, stocks=Stocks(fuel=20, steel=20)
    )


def _army(count: int, first: int = 1) -> list[Unit]:
    """``count`` infantry spread around p's base and front farm."""
    spots = [at(c, r) for r in range(1, 9) for c in range(1, 6)]
    return [make_unit(first + k, INF, spots[k]) for k in range(count)]


def _operation(state: GameState, aggression: float, **kw: object) -> Operation | None:
    view = redact_state(state, "p")
    return plan_operation(view, "p", aggression, 20, 0.0, **kw)  # type: ignore[arg-type]


# --- The strategist: whether and where to fight ------------------------------------


def test_a_bot_at_war_size_goes_for_the_nearest_enemy_settlement() -> None:
    op = _operation(_world(_army(8)), BOLD)
    assert op is not None
    assert op.target == "q" and op.objective == Q_FARM
    assert op.staging == FRONT  # assembles on its own farm, 5 hexes short
    assert op.assemble <= op.force <= 8


def test_a_cautious_bot_waits_for_a_bigger_army() -> None:
    assert _operation(_world(_army(8)), CAUTIOUS) is None
    assert _operation(_world(_army(11)), CAUTIOUS) is not None


def test_a_war_under_way_goes_on_with_a_smaller_army() -> None:
    near = [make_unit(20, INF, at(9, 6)), make_unit(21, INF, at(9, 7))]
    assert distance(at(9, 6), Q_FARM) <= 7
    op = _operation(_world(_army(6) + near), CAUTIOUS)
    assert op is not None and op.objective == Q_FARM


def test_no_war_of_choice_while_cheap_land_remains() -> None:
    assert _operation(_world(_army(8)), BOLD, expanding=True) is None


def test_a_clearly_beaten_neighbour_is_finished_off_even_while_expanding() -> None:
    more = {at(3, 9): "p", at(5, 10): "p", at(2, 6): "p", at(4, 4): "p"}
    state = _world(_army(4), more)
    for settlement in state.settlements:
        if settlement.coord == Q_FARM:
            settlement.controlled_by = "p"
    # p holds 7, q only its base: p presses on to it with a small army.
    op = _operation(state, CAUTIOUS, expanding=True)
    assert op is not None and op.objective == Q_BASE


def test_no_operation_while_the_home_base_is_in_danger() -> None:
    raiders = [make_unit(30, INF, at(3, 3), "q"), make_unit(31, INF, at(4, 2), "q")]
    assert _operation(_world(_army(8) + raiders), BOLD) is None


def test_a_defence_too_strong_for_the_army_is_passed_over() -> None:
    watch = make_unit(20, INF, at(10, 6))  # sees the farm's defenders
    defenders = [
        make_unit(30 + k, INF, c, "q")
        for k, c in enumerate([Q_FARM, *neighbors(Q_FARM)])
        if c != at(10, 6)
    ]
    assert len(defenders) == 7  # they need 9 attackers; p has 8
    op = _operation(_world(_army(7) + [watch] + defenders), BOLD)
    assert op is not None and op.objective == Q_BASE


def test_the_strategist_hands_the_operation_to_the_tactical_layer() -> None:
    state = _world(_army(8))
    bold = ScriptedStrategist(Personality(aggression=BOLD, reach=20, garrison_share=0))
    args = bold.choose(redact_state(state, "p"), "p")
    assert args.stance is Stance.ATTACK and args.operation is not None
    plan = plan_world_turn(redact_state(state, "p"), "p", args)
    assert plan.operation == args.operation


# --- The tactical layer: gather, close up, strike together -------------------------


def _plan(state: GameState, op: Operation) -> dict[int, Order]:
    view = redact_state(state, "p")
    plan = plan_world_turn(
        view, "p", StrategistArgs(stance=Stance.ATTACK, operation=op)
    )
    validate_submission(view, "p", plan.orders, plan.recruitments, None)
    return {o.unit_id: o for o in plan.orders}


def _strikes(order: Order, target: Axial) -> bool:
    return isinstance(order, MoveOrder) and order.path[-1] == target


OP = Operation(target="q", objective=Q_FARM, staging=FRONT, force=3, assemble=3)


def test_the_force_gathers_before_it_goes_in() -> None:
    first = make_unit(1, INF, at(10, 6))  # in the zone, alone
    late = [make_unit(2, INF, at(1, 3)), make_unit(3, INF, at(1, 4))]
    defender = make_unit(9, INF, Q_FARM, "q")
    orders = _plan(_world([first, *late, defender]), OP)
    assert not _strikes(orders[1], Q_FARM)
    # The early unit falls back to the staging area; the rest march there.
    move = orders[1]
    assert isinstance(move, MoveOrder)
    assert distance(move.path[-1], FRONT) < distance(first.coord, FRONT)
    for k, unit in ((2, late[0]), (3, late[1])):
        order = orders[k]
        if isinstance(order, TravelOrder):
            assert order.destination == FRONT
        else:
            assert isinstance(order, MoveOrder)
            assert distance(order.path[-1], FRONT) < distance(unit.coord, FRONT)


def _ring() -> list[Axial]:
    """Three hexes two out from q's farm, on p's side, not touching each other."""
    out = sorted(
        (c for c in within(Q_FARM, 2) if distance(c, Q_FARM) == 2 and c.q <= Q_FARM.q),
        key=lambda c: (c.q, c.r),
    )
    ring: list[Axial] = []
    for c in out:
        if all(distance(c, r) >= 2 for r in ring):
            ring.append(c)
    return ring[:3]


def test_an_assembled_force_strikes_one_defender_together() -> None:
    ring = _ring()
    assert len(ring) == 3
    force = [make_unit(k + 1, INF, c) for k, c in enumerate(ring)]
    orders = _plan(_world([*force, make_unit(9, INF, Q_FARM, "q")]), OP)
    assert all(_strikes(orders[k + 1], Q_FARM) for k in range(3))


def test_an_outnumbered_force_closes_up_but_does_not_strike() -> None:
    force = [make_unit(k + 1, INF, c) for k, c in enumerate(_ring())]
    defenders = [
        make_unit(30 + k, INF, c, "q")
        for k, c in enumerate([Q_FARM, *neighbors(Q_FARM)[:2]])
    ]
    orders = _plan(_world([*force, *defenders]), OP)
    enemy = {d.coord for d in defenders}
    for k in range(3):
        order = orders[k + 1]
        assert not (isinstance(order, MoveOrder) and order.path[-1] in enemy)


def test_a_closing_force_halts_out_of_contact() -> None:
    far = [at(8, 3), at(8, 4), at(8, 5)]
    force = [make_unit(k + 1, INF, c) for k, c in enumerate(far)]
    defenders = [make_unit(30, INF, Q_FARM, "q"), make_unit(31, INF, at(13, 6), "q")]
    orders = _plan(_world([*force, *defenders]), OP)
    for k in range(3):
        order = orders[k + 1]
        if isinstance(order, MoveOrder):
            end = order.path[-1]
            assert all(distance(end, d.coord) >= 2 for d in defenders)
