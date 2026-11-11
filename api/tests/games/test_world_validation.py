"""Submission validation for world games: recruits by site kind, base orders."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.engine.economy_rules import Building, Stocks
from app.engine.events import WorldUnitRecruited
from app.engine.execution import resolve_execution
from app.engine.models import Construction, EconomyOrders, GameState, Recruitment
from app.engine.stats import UnitType
from app.games.validation import OrderValidationError, validate_submission
from engine.test_world_economy import (
    BASE,
    CITY,
    FACTORY,
    FARM,
    TOWN,
    VACANT,
    unit,
    world,
)

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


def test_towns_and_vacant_base_slots_take_no_recruits() -> None:
    state = world(stocks=RICH)
    with pytest.raises(OrderValidationError, match="recruits only at"):
        _validate(state, [Recruitment(owner="p", type=INF, coord=TOWN)])
    with pytest.raises(OrderValidationError, match="not held"):
        _validate(state, [Recruitment(owner="p", type=INF, coord=VACANT)])


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


def _full_army(construction: Construction, turn: int) -> GameState:
    # Four units fill the army size of 4 (no barracks, only the base held).
    units = [unit(i, INF, 2 + i, 3) for i in range(1, 5)]
    return world(units=units, stocks=RICH, construction=construction, turn=turn)


def test_a_recruit_counts_a_barracks_that_finishes_this_turn() -> None:
    # The economy phase completes the barracks before recruits appear, so the
    # engine checks the bigger army; validation must agree (playtest 2026-11-11).
    barracks = Construction(building=Building.BARRACKS, level=1, completes_on=3)
    state = _full_army(barracks, turn=3)
    recruit = Recruitment(owner="p", type=INF, coord=BASE)
    _validate(state, [recruit])

    _, events = resolve_execution(state, [], [recruit])
    assert [e.kind for e in events if isinstance(e, WorldUnitRecruited)] == [
        "world_unit_recruited"
    ]


def test_a_recruit_does_not_count_a_barracks_finishing_later() -> None:
    barracks = Construction(building=Building.BARRACKS, level=1, completes_on=4)
    with pytest.raises(OrderValidationError, match="army size of 4"):
        _validate(
            _full_army(barracks, turn=3), [Recruitment(owner="p", type=INF, coord=BASE)]
        )


def test_a_gun_recruit_counts_a_workshop_that_finishes_this_turn() -> None:
    workshop = Construction(building=Building.WORKSHOP, level=1, completes_on=3)
    recruit = Recruitment(owner="p", type=ART, coord=BASE)
    _validate(world(stocks=RICH, construction=workshop, turn=3), [recruit])
    later = Construction(building=Building.WORKSHOP, level=1, completes_on=4)
    with pytest.raises(OrderValidationError, match="needs a workshop"):
        _validate(world(stocks=RICH, construction=later, turn=3), [recruit])
