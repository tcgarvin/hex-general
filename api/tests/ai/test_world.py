"""World bots and the world simulator (WORLD_SPEC §8–9).

Small generated worlds keep these fast. The simulator validates every plan with
`validate_submission`, so a run that completes is itself a legality check.
"""

from __future__ import annotations

import pytest

from app.ai.world import (
    Personality,
    ScriptedStrategist,
    Stance,
    StrategistArgs,
    WorldPolicy,
    plan_world_turn,
)
from app.engine.fog import redact_state
from app.engine.hex_coords import distance
from app.engine.models import GameState, PlayerId, TravelOrder
from app.engine.stats import Terrain
from app.games.validation import validate_submission
from app.world.generate import GeneratedWorld, WorldParams, generate
from app.world.sim import SimReport, run_sim


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams.for_slots(6, 3))


def _timeless(report: SimReport) -> dict[str, object]:
    data = report.model_dump()
    for key in ("think_ms_mean", "think_ms_p95", "resolve_ms_max"):
        data.pop(key)
    data.pop("gates")
    for turn in data["turn_stats"]:
        turn.pop("resolve_ms")
        turn.pop("think_ms_max")
    return data


def test_personalities_are_seeded_and_varied() -> None:
    assert Personality.seeded(1, "b000") == Personality.seeded(1, "b000")
    looks = {Personality.seeded(1, f"b{k:03d}") for k in range(8)}
    assert len(looks) > 1


def test_bots_expand_first(world: GeneratedWorld) -> None:
    state = world.to_state(["b000", "b001"])
    view = redact_state(state, "b000")
    strategist = ScriptedStrategist(Personality.seeded(1, "b000"))
    assert strategist.choose(view, "b000").stance is Stance.EXPAND


def test_bots_attack_when_no_neutral_land_is_left(world: GeneratedWorld) -> None:
    state = world.to_state(["b000", "b001"])
    for settlement in state.settlements:
        if settlement.controlled_by is None:
            settlement.controlled_by = "b001"
    bold = Personality(aggression=0.9, reach=20, garrison_share=0.0)
    args = ScriptedStrategist(bold).choose(redact_state(state, "b000"), "b000")
    assert args.stance is Stance.ATTACK
    assert args.focus is not None


def test_cautious_bot_defends_a_threatened_holding(world: GeneratedWorld) -> None:
    state = world.to_state(["b000", "b001"])
    own_capital = next(s.coord for s in state.settlements if s.controlled_by == "b000")
    raider = next(u for u in state.units if u.owner == "b001")
    raider.coord = next(
        h.coord
        for h in state.map.hexes
        if 2 <= distance(h.coord, own_capital) <= 3 and h.terrain is Terrain.PLAINS
    )
    cautious = Personality(aggression=0.2, reach=20, garrison_share=0.0)
    # The raider is visible from the capital garrison (vision 3).
    args = ScriptedStrategist(cautious).choose(redact_state(state, "b000"), "b000")
    assert args.stance is Stance.DEFEND


def test_distant_objectives_get_travel_orders(world: GeneratedWorld) -> None:
    state = world.to_state(["b000", "b001"])
    view = redact_state(state, "b000")
    plan = plan_world_turn(view, "b000", StrategistArgs(stance=Stance.EXPAND))
    validate_submission(view, "b000", plan.orders, plan.recruitments)
    assert any(isinstance(o, TravelOrder) for o in plan.orders)


class _FixedStrategist:
    """Stands in for the LLM strategist: the seam takes any `Strategist`."""

    def __init__(self) -> None:
        self.calls: list[PlayerId] = []

    def choose(self, view: GameState, slot: PlayerId) -> StrategistArgs:
        self.calls.append(slot)
        return StrategistArgs(stance=Stance.DEFEND, reserve_share=0.5)


def test_world_policy_takes_any_strategist(world: GeneratedWorld) -> None:
    state = world.to_state(["b000", "b001"])
    strategist = _FixedStrategist()
    policy = WorldPolicy(strategist=strategist)
    view = redact_state(state, "b000")
    plan = policy.plan(view, "b000")
    validate_submission(view, "b000", plan.orders, plan.recruitments)
    assert strategist.calls == ["b000"]


def test_sim_runs_legal_plans_and_is_deterministic(world: GeneratedWorld) -> None:
    first = run_sim(6, 3, turns=12, world=world)
    second = run_sim(6, 3, turns=12, world=world)
    assert _timeless(first) == _timeless(second)
    assert len(first.turn_stats) == 12
    assert first.travel_issued > 0
    assert sum(first.travel_ended.values()) > 0
    assert first.turn_stats[-1].settlements  # somebody holds something
