"""Baseline policy tests: legality, determinism, statelessness (AI_SPEC §7.1)."""

from __future__ import annotations

from app.ai.arena import load_scenario_state
from app.ai.baselines import PassivePolicy, RandomPolicy
from app.engine.fog import redact_state
from app.engine.models import GameState, HoldOrder
from app.games.validation import validate_submission
from engine.helpers import build


def _twin_view(slot: str) -> tuple[GameState, GameState]:
    state = load_scenario_state("twin_rivers")
    return state, redact_state(state, slot)


def test_passive_holds_every_owned_unit() -> None:
    state = build(
        ["....", "....", "...."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "armor", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 2},
        ],
    )
    plan = PassivePolicy().plan(state, "blue")
    assert len(plan.orders) == 2
    assert all(isinstance(o, HoldOrder) for o in plan.orders)
    assert {o.unit_id for o in plan.orders} == {1, 2}
    validate_submission(state, "blue", plan.orders, plan.recruitments)


def test_random_plan_is_statically_legal() -> None:
    state, view = _twin_view("blue")
    plan = RandomPolicy(seed=13).plan(view, "blue")
    # One order per owned unit, all legal against the view.
    own = {u.id for u in view.units if u.owner == "blue"}
    assert {o.unit_id for o in plan.orders} == own
    validate_submission(view, "blue", plan.orders, plan.recruitments)


def test_random_is_deterministic_given_seed_view_slot() -> None:
    _state, view = _twin_view("blue")
    a = RandomPolicy(seed=99).plan(view, "blue")
    b = RandomPolicy(seed=99).plan(view, "blue")
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_random_differs_by_seed() -> None:
    _state, view = _twin_view("blue")
    a = RandomPolicy(seed=1).plan(view, "blue")
    b = RandomPolicy(seed=2).plan(view, "blue")
    assert a.model_dump(mode="json") != b.model_dump(mode="json")


def test_random_stateless_across_calls() -> None:
    """Reusing one instance across turns is deterministic (no carried RNG state)."""
    _state, view = _twin_view("red")
    policy = RandomPolicy(seed=5)
    first = policy.plan(view, "red")
    second = policy.plan(view, "red")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
