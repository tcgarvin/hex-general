"""World bots' base orders are legal and spend sensibly."""

from __future__ import annotations

import pytest

from app.ai.base_plan import plan_base, site_needs
from app.ai.policy import economy_orders_for
from app.ai.world import WorldPolicy
from app.engine.economy_rules import Building, SiteKind, Stocks
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.models import EconomyOrders
from app.games.validation import validate_submission
from app.engine.stats import UnitType
from app.world.generate import GeneratedWorld, WorldParams, generate
from engine.test_world_economy import BASE, FARM, unit
from engine.test_world_economy import world as small_world


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(4, seed=11))


def test_a_new_bot_starts_its_barracks(world: GeneratedWorld) -> None:
    state = world.to_state(["p0", "p1"])
    plan = plan_base(redact_state(state, "p0"), "p0")
    assert plan.build is Building.BARRACKS
    assert plan.recruitments == []  # army size 4 is already full


def test_world_bot_plans_stay_legal_for_many_turns(world: GeneratedWorld) -> None:
    ids = ["p0", "p1", "p2"]
    state = world.to_state(ids)
    policy = WorldPolicy(seed=11)
    built = recruited = 0
    for _ in range(30):
        orders, recruits, economy = [], [], []
        for slot in ids:
            view = redact_state(state, slot)
            plan = policy.plan(view, slot)
            base_orders = plan.economy_orders(slot)
            validate_submission(view, slot, plan.orders, plan.recruitments, base_orders)
            orders += plan.orders
            recruits += plan.recruitments
            economy += [base_orders] if base_orders is not None else []
        state, _ = resolve_execution(state, orders, recruits, economy)
        built = max(built, max(p.level(Building.BARRACKS) for p in state.players))
        recruited += len(recruits)
    assert built >= 2
    assert recruited >= 3


def test_needs_favour_farms_when_manpower_is_short(world: GeneratedWorld) -> None:
    state = world.to_state(["p0"])
    needs = site_needs(state, "p0")
    # Four units eat 4 of the base's 5 manpower; no factory held yet.
    assert needs[SiteKind.FARM] > 1.0
    assert needs[SiteKind.FACTORY] > 1.0


def test_needs_count_a_sites_level_in_its_income() -> None:
    infantry = [unit(k, UnitType.INFANTRY, 2 + k % 3, 2 + k // 3) for k in range(6)]
    # Six infantry eat 6 manpower: the base's 5 plus a level-1 farm's 2 leave 1.
    low = small_world(held=(BASE, FARM), units=infantry)
    # A level-3 farm earns 4, so manpower is no longer as tight.
    high = small_world(held=(BASE, FARM), units=infantry, levels={FARM: 3})
    assert site_needs(low, "p")[SiteKind.FARM] > site_needs(high, "p")[SiteKind.FARM]


def test_needs_follow_the_flow_and_the_stock() -> None:
    infantry = [unit(k, UnitType.INFANTRY, 2 + k % 3, 2 + k // 3) for k in range(7)]
    # Seven infantry eat 7 manpower against the base's 5: disbands loom.
    bleeding = site_needs(small_world(units=infantry), "p")
    assert bleeding[SiteKind.FARM] > 2.0
    # A full manpower stock with a positive flow needs no farm.
    full = small_world(units=infantry[:2], stocks=Stocks(manpower=30, steel=30))
    assert site_needs(full, "p")[SiteKind.FARM] < 1.0
    # Steel short of the next building makes a steel mill worth more.
    poor = site_needs(small_world(units=infantry[:2], stocks=Stocks()), "p")
    rich = site_needs(small_world(units=infantry[:2], stocks=Stocks(steel=20)), "p")
    assert poor[SiteKind.STEEL_MILL] > rich[SiteKind.STEEL_MILL]


def test_barracks_two_comes_before_the_workshop() -> None:
    rich = Stocks(manpower=20, fuel=20, steel=20)
    after_one = small_world(stocks=rich, buildings={Building.BARRACKS: 1})
    assert plan_base(after_one, "p").build is Building.BARRACKS
    after_two = small_world(stocks=rich, buildings={Building.BARRACKS: 2})
    assert plan_base(after_two, "p").build is Building.WORKSHOP


def test_economy_orders_helper_is_none_without_base_orders() -> None:
    assert economy_orders_for("p", None, []) is None
    assert economy_orders_for("p", Building.DEPOT, []) == EconomyOrders(
        owner="p", build=Building.DEPOT
    )
