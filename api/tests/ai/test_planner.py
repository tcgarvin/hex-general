"""SearchPolicy tests: refinement, anti-self-jam, determinism (AI_SPEC §4.7, §7.2).

The anti-self-jam scenario (§7.2 case 4) is the headline behaviour that separates
`SearchPolicy` from `ScriptedPolicy`: two units whose greedy advances collide on
the same hex self-jam under the real engine, and the simulation-scored refinement
must pick a plan that does not.
"""

from __future__ import annotations

from app.ai.planner import SearchPolicy
from app.ai.scripted import ScriptedPolicy
from app.engine.events import TrafficJam
from app.engine.execution import resolve_execution
from app.engine.models import GameState, Order, PlayerId, Recruitment
from app.games.validation import validate_submission
from engine.helpers import build

_NO_ELIM = {"turn_limit": 12, "city_elimination": False}


def _own_traffic_jams(
    state: GameState,
    orders: list[Order],
    recruitments: list[Recruitment],
    slot: PlayerId,
) -> list[TrafficJam]:
    own_ids = {u.id for u in state.units if u.owner == slot}
    _resolved, events = resolve_execution(state, orders, recruitments)
    jams = [e for e in events if isinstance(e, TrafficJam)]
    return [j for j in jams if any(uid in own_ids for uid in j.unit_ids)]


def _collision_state() -> GameState:
    # Two blue infantry at (0,0) and (0,1) both want the neutral town at (5,0).
    # The river at (2,1) removes B's sideways option, so both units' best advance
    # lands on (2,0): taken together they collide there (SPEC §8.2 traffic jam).
    return build(
        [".....t", "..~..."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 1},
        ],
        settlements=[{"q": 5, "r": 0}],
        players=[{"id": "blue"}, {"id": "red"}],
        victory=_NO_ELIM,
    )


def test_scripted_baseline_actually_self_jams() -> None:
    # Guards the scenario: the greedy scripted plan really does collide, so the
    # SearchPolicy assertion below is meaningful and not vacuous.
    state = _collision_state()
    plan = ScriptedPolicy().plan(state, "blue")
    jams = _own_traffic_jams(state, plan.orders, plan.recruitments, "blue")
    assert jams, "scripted plan should self-jam in this scenario"


def test_search_refines_away_self_jam() -> None:
    state = _collision_state()
    plan = SearchPolicy().plan(state, "blue")
    validate_submission(state, "blue", plan.orders, plan.recruitments)
    jams = _own_traffic_jams(state, plan.orders, plan.recruitments, "blue")
    assert not jams, "search should not return a self-jamming plan"


def test_search_plan_is_legal_and_deterministic() -> None:
    state = _collision_state()
    policy = SearchPolicy()
    first = policy.plan(state, "blue")
    second = SearchPolicy().plan(state, "blue")
    validate_submission(state, "blue", first.orders, first.recruitments)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
