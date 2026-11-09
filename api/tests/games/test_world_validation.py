"""Submission validation for world games: recruits by site kind, base orders."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.engine.economy_rules import Building, Stocks
from app.engine.models import Construction, EconomyOrders, GameState, Recruitment
from app.engine.stats import UnitType
from app.games.validation import OrderValidationError, validate_submission
from engine.test_world_economy import BASE, CITY, FACTORY, FARM, unit, world

INF, ARM, ART = UnitType.INFANTRY, UnitType.ARMOR, UnitType.ARTILLERY
RICH = Stocks(manpower=30, fuel=30, steel=30)


def _validate(
    state: GameState,
    recruits: Sequence[Recruitment] = (),
    economy: EconomyOrders | None = None,
) -> None:
    validate_submission(state, "p", [], list(recruits), economy)


def test_a_build_order_is_legal_when_affordable_and_idle() -> None:
    _validate(
        world(stocks=RICH), economy=EconomyOrders(owner="p", build=Building.BARRACKS)
    )


def test_building_while_busy_is_rejected_unless_it_finishes_this_turn() -> None:
    busy = Construction(building=Building.DEPOT, level=1, completes_on=5)
    with pytest.raises(OrderValidationError, match="under construction"):
        _validate(
            world(stocks=RICH, construction=busy, turn=3),
            economy=EconomyOrders(owner="p", build=Building.BARRACKS),
        )
    finishing = Construction(building=Building.WORKSHOP, level=1, completes_on=3)
    with pytest.raises(OrderValidationError, match="top level"):
        _validate(
            world(stocks=RICH, construction=finishing, turn=3),
            economy=EconomyOrders(owner="p", build=Building.WORKSHOP),
        )
    _validate(
        world(stocks=RICH, construction=finishing, turn=3),
        economy=EconomyOrders(owner="p", build=Building.DEPOT),
    )


def test_unaffordable_builds_and_foreign_base_orders_are_rejected() -> None:
    with pytest.raises(OrderValidationError, match="costs"):
        _validate(world(), economy=EconomyOrders(owner="p", build=Building.BARRACKS))
    with pytest.raises(OrderValidationError, match="not owned"):
        _validate(world(stocks=RICH), economy=EconomyOrders(owner="q"))


def test_disband_picks_must_be_own_distinct_units() -> None:
    state = world(units=[unit(1, INF, 2, 2), unit(2, INF, 3, 3, owner="q")])
    _validate(state, economy=EconomyOrders(owner="p", disband=[1]))
    with pytest.raises(OrderValidationError, match="not yours"):
        _validate(state, economy=EconomyOrders(owner="p", disband=[2]))
    with pytest.raises(OrderValidationError, match="twice"):
        _validate(state, economy=EconomyOrders(owner="p", disband=[1, 1]))


def test_recruits_need_the_right_held_site() -> None:
    held = world(held=(BASE, FACTORY, CITY, FARM), stocks=RICH)
    _validate(
        held,
        [
            Recruitment(owner="p", type=INF, coord=BASE),
            Recruitment(owner="p", type=ARM, coord=FACTORY),
        ],
    )
    with pytest.raises(OrderValidationError, match="recruits only at"):
        _validate(held, [Recruitment(owner="p", type=ARM, coord=CITY)])
    with pytest.raises(OrderValidationError, match="recruits only at"):
        _validate(held, [Recruitment(owner="p", type=INF, coord=FARM)])
    with pytest.raises(OrderValidationError, match="workshop"):
        _validate(held, [Recruitment(owner="p", type=ART, coord=CITY)])
    with pytest.raises(OrderValidationError, match="not held"):
        _validate(world(stocks=RICH), [Recruitment(owner="p", type=INF, coord=CITY)])


def test_recruits_and_the_build_share_one_budget() -> None:
    state = world(held=(BASE, FACTORY), stocks=Stocks(manpower=10, steel=12))
    _validate(state, [Recruitment(owner="p", type=ARM, coord=FACTORY)])
    with pytest.raises(OrderValidationError, match="stocks are"):
        _validate(
            state,
            [Recruitment(owner="p", type=ARM, coord=FACTORY)],
            EconomyOrders(owner="p", build=Building.BARRACKS),
        )


def test_base_orders_are_refused_in_a_v01_game() -> None:
    state = world()
    v01 = state.model_copy(
        update={"config": state.config.model_copy(update={"economy": None})}
    )
    with pytest.raises(OrderValidationError, match="world games"):
        _validate(v01, economy=EconomyOrders(owner="p"))
