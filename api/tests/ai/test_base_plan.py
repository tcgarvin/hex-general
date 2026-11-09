"""World bots' base orders are legal and spend sensibly."""

from __future__ import annotations

import pytest

from app.ai.base_plan import plan_base, site_needs
from app.ai.policy import economy_orders_for
from app.ai.world import WorldPolicy
from app.engine.economy_rules import Building, SiteKind
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.models import EconomyOrders
from app.games.validation import validate_submission
from app.world.generate import GeneratedWorld, WorldParams, generate


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
    assert needs[SiteKind.FARM] == 2.0
    assert needs[SiteKind.FACTORY] > 1.0


def test_economy_orders_helper_is_none_without_base_orders() -> None:
    assert economy_orders_for("p", None, []) is None
    assert economy_orders_for("p", Building.DEPOT, []) == EconomyOrders(
        owner="p", build=Building.DEPOT
    )
