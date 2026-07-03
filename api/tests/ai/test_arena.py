"""Arena tests: termination, determinism, aggregation (AI_SPEC §6, §7.1).

Uses a tiny synthetic map for the per-game legality/determinism checks (the
1600-hex twin_rivers map makes each engine step ~150ms; a small map keeps the
default suite fast) and twin_rivers only for a minimal `run_match` smoke.
"""

from __future__ import annotations

from app.ai.arena import play_game, run_match
from app.ai.baselines import PassivePolicy, RandomPolicy
from app.ai.policy import Policy
from app.engine.models import GameState, PlayerId
from engine.helpers import build


def _small_state() -> GameState:
    return build(
        [
            "c....",
            ".....",
            ".....",
            ".....",
            "....c",
        ],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "armor", "owner": "blue", "q": 2, "r": 1},
            {"type": "infantry", "owner": "red", "q": 3, "r": 4},
            {"type": "armor", "owner": "red", "q": 2, "r": 3},
        ],
        settlements=[
            {"q": 0, "r": 0, "owner": "blue"},
            {"q": 4, "r": 4, "owner": "red"},
        ],
        players=[{"id": "blue", "bank": 6}, {"id": "red", "bank": 6}],
        victory={"turn_limit": 4},
    )


def _policies(seed: int) -> dict[PlayerId, Policy]:
    return {"blue": RandomPolicy(seed), "red": PassivePolicy()}


def test_play_game_terminates_and_is_decided() -> None:
    state = _small_state()
    result = play_game(state, _policies(3), seed=3)
    assert result.reason != "ongoing"
    assert result.turns_played <= state.config.victory.turn_limit
    assert set(result.scores) == {"blue", "red"}
    assert set(result.think_ms) == {"blue", "red"}
    assert len(result.order_log) == result.turns_played


def test_play_game_is_deterministic() -> None:
    state = _small_state()
    first = play_game(state, _policies(11), seed=11)
    second = play_game(state, _policies(11), seed=11)
    assert first.signature() == second.signature()


def test_run_match_alternates_colours_and_aggregates() -> None:
    # twin_rivers (the full 40x40 map) is intentionally used sparingly here — each
    # engine step is ~150ms; per-game determinism is covered above on the small map.
    result = run_match("random", "passive", "twin_rivers", 2, base_seed=7)
    assert result.games == 2
    assert result.wins_a + result.draws + result.losses_a == 2
    assert 0.0 <= result.win_pct_a <= 1.0
    assert result.think_mean_ms["random"] >= 0.0
