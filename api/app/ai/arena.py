"""Headless match runner + CLI (AI_SPEC §6).

Two layers:

* `play_game` drives one full game between a set of policies under the real
  engine — redact per slot, `plan()`, assert `validate_submission`, resolve, check
  victory — and returns a `GameResult` (outcome, scores, losses, per-turn think
  time, and the full order log for replay/determinism checks).
* `run_match` plays ``n`` games of a pairing, alternating colours each game (the
  scenario is asymmetric, so each policy must play both slots equally) and varying
  the seed, and aggregates a `MatchResult` (W/D/L, win%, VP margin, game length,
  think-time stats).

The CLI (``uv run python -m app.ai.arena``) resolves policy names through
`REGISTRY` and prints the aggregate table; ``--json`` dumps the full result.
This module is the acceptance-gate harness (§7.3), the balance-playtest tool, and
the weight-tuning fitness function (§2.4).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.ai.baselines import PassivePolicy, RandomPolicy
from app.ai.policy import BotPlan, Policy
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.models import GameState, Order, PlayerId, Recruitment
from app.engine.scenario import load_scenario_file
from app.engine.victory import victory_status
from app.games.validation import validate_submission

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"

# A policy factory takes a per-game seed and returns a fresh Policy. Baselines
# ignore the seed except `RandomPolicy`; smart policies pass it to tie-break
# jitter. Later phases extend this registry with "scripted" and "search".
PolicyFactory = Callable[[int], Policy]

REGISTRY: dict[str, PolicyFactory] = {
    "passive": lambda seed: PassivePolicy(),
    "random": lambda seed: RandomPolicy(seed),
}


class TurnLog(BaseModel):
    """One turn's submitted plans, keyed by slot (for replay + determinism)."""

    model_config = ConfigDict(extra="forbid")

    turn: int
    plans: dict[PlayerId, BotPlan]


class GameResult(BaseModel):
    """The outcome of one played game (AI_SPEC §6)."""

    model_config = ConfigDict(extra="forbid")

    winner: PlayerId | None
    reason: str
    scores: dict[PlayerId, int]
    turns_played: int
    units_lost: dict[PlayerId, int]
    think_ms: dict[PlayerId, list[float]]
    order_log: list[TurnLog]

    def signature(self) -> str:
        """A determinism fingerprint: everything but wall-clock think time.

        Two runs of the same ``(policies, scenario, seed)`` must produce the same
        signature (AI_SPEC §7.1); think time is inherently non-deterministic and
        is excluded.
        """
        payload = {
            "winner": self.winner,
            "reason": self.reason,
            "scores": self.scores,
            "turns_played": self.turns_played,
            "units_lost": self.units_lost,
            "order_log": [t.model_dump(mode="json") for t in self.order_log],
        }
        return json.dumps(payload, sort_keys=True)


def play_game(
    scenario_state: GameState,
    policies: dict[PlayerId, Policy],
    *,
    seed: int = 0,
    max_turns_guard: int | None = None,
) -> GameResult:
    """Play one game to a decided victory, returning its `GameResult`.

    Each turn: redact the true state per slot, call `plan()`, assert the plan is
    statically legal (`validate_submission`), then resolve every side together and
    re-check victory. The engine's turn limit guarantees termination;
    ``max_turns_guard`` is a crash-loud backstop (default: turn limit + 5), not a
    silent truncation — exceeding it raises `RuntimeError`.
    """
    state = scenario_state
    slots = sorted(policies)
    guard = (
        max_turns_guard
        if max_turns_guard is not None
        else state.config.victory.turn_limit + 5
    )

    think_ms: dict[PlayerId, list[float]] = {slot: [] for slot in slots}
    units_lost: dict[PlayerId, int] = {slot: 0 for slot in slots}
    order_log: list[TurnLog] = []

    while True:
        status = victory_status(state)
        if status.decided:
            break
        if state.turn > guard:
            raise RuntimeError(
                f"arena guard tripped at turn {state.turn} (> {guard}); "
                "the game failed to terminate"
            )

        before_ids = {
            slot: {u.id for u in state.units if u.owner == slot} for slot in slots
        }

        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        plans: dict[PlayerId, BotPlan] = {}
        for slot in slots:
            view = redact_state(state, slot)
            start = time.perf_counter()
            plan = policies[slot].plan(view, slot)
            think_ms[slot].append((time.perf_counter() - start) * 1000.0)
            # Legality is an invariant, not a warning: a bad plan crashes here.
            validate_submission(view, slot, plan.orders, plan.recruitments)
            plans[slot] = plan
            orders.extend(plan.orders)
            recruitments.extend(plan.recruitments)

        order_log.append(TurnLog(turn=state.turn, plans=plans))
        state, _events = resolve_execution(state, orders, recruitments)

        after_ids = {
            slot: {u.id for u in state.units if u.owner == slot} for slot in slots
        }
        for slot in slots:
            units_lost[slot] += len(before_ids[slot] - after_ids[slot])

    final = victory_status(state)
    return GameResult(
        winner=final.winner,
        reason=final.reason.value,
        scores=final.scores,
        turns_played=state.turn,
        units_lost=units_lost,
        think_ms=think_ms,
        order_log=order_log,
    )


class MatchResult(BaseModel):
    """Aggregate outcome of a `run_match` pairing (AI_SPEC §6, §7.3)."""

    model_config = ConfigDict(extra="forbid")

    a_name: str
    b_name: str
    scenario: str
    games: int
    wins_a: int
    draws: int
    losses_a: int
    win_pct_a: float  # draw counts as half (AI_SPEC §7.3)
    mean_margin_a: float  # mean (a_score - b_score)
    mean_turns: float
    think_mean_ms: dict[str, float]
    think_p95_ms: dict[str, float]
    think_max_ms: dict[str, float]
    results: list[GameResult] = Field(default_factory=list)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile of ``values`` (0.0 for an empty list)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(pct * (len(ordered) - 1)))
    return ordered[idx]


def load_scenario_state(scenario: str) -> GameState:
    """Load a named scenario's turn-0 state from ``app/scenarios``."""
    path = _SCENARIOS_DIR / f"{scenario}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no scenario named {scenario!r} at {path}")
    return load_scenario_file(path)


def run_match(
    a_name: str,
    b_name: str,
    scenario: str,
    n_games: int,
    base_seed: int,
    *,
    keep_results: bool = False,
) -> MatchResult:
    """Play ``n_games`` of ``a_name`` vs ``b_name``, alternating colours.

    Game ``i`` uses ``seed = base_seed + i``; even games seat A as blue, odd games
    seat A as red, so each policy plays both slots of the asymmetric scenario
    equally. Aggregates W/D/L and margins from A's perspective and think-time
    stats per policy name.
    """
    if a_name not in REGISTRY:
        raise KeyError(f"unknown policy {a_name!r}; known: {sorted(REGISTRY)}")
    if b_name not in REGISTRY:
        raise KeyError(f"unknown policy {b_name!r}; known: {sorted(REGISTRY)}")

    scenario_state = load_scenario_state(scenario)
    slot_ids = sorted(p.id for p in scenario_state.players)
    if len(slot_ids) != 2:
        raise ValueError("run_match expects a two-slot scenario")
    blue, red = slot_ids

    wins_a = draws = losses_a = 0
    margin_sum = 0.0
    turns_sum = 0
    think: dict[str, list[float]] = {a_name: [], b_name: []}
    results: list[GameResult] = []

    for game_index in range(n_games):
        seed = base_seed + game_index
        a_is_blue = game_index % 2 == 0
        a_slot = blue if a_is_blue else red
        b_slot = red if a_is_blue else blue
        policies: dict[PlayerId, Policy] = {
            a_slot: REGISTRY[a_name](seed),
            b_slot: REGISTRY[b_name](seed),
        }
        result = play_game(scenario_state, policies, seed=seed)

        if result.winner == a_slot:
            wins_a += 1
        elif result.winner == b_slot:
            losses_a += 1
        else:
            draws += 1
        margin_sum += result.scores[a_slot] - result.scores[b_slot]
        turns_sum += result.turns_played
        think[a_name].extend(result.think_ms[a_slot])
        think[b_name].extend(result.think_ms[b_slot])
        if keep_results:
            results.append(result)

    win_pct_a = (wins_a + 0.5 * draws) / n_games if n_games else 0.0
    return MatchResult(
        a_name=a_name,
        b_name=b_name,
        scenario=scenario,
        games=n_games,
        wins_a=wins_a,
        draws=draws,
        losses_a=losses_a,
        win_pct_a=win_pct_a,
        mean_margin_a=margin_sum / n_games if n_games else 0.0,
        mean_turns=turns_sum / n_games if n_games else 0.0,
        think_mean_ms={
            name: (sum(v) / len(v) if v else 0.0) for name, v in think.items()
        },
        think_p95_ms={name: _percentile(v, 0.95) for name, v in think.items()},
        think_max_ms={name: (max(v) if v else 0.0) for name, v in think.items()},
        results=results,
    )


def _format_match(result: MatchResult) -> str:
    """A human-readable aggregate table for the CLI."""
    lines = [
        f"Scenario: {result.scenario}   Games: {result.games}",
        f"{result.a_name} (A) vs {result.b_name} (B)",
        f"  A W/D/L: {result.wins_a}/{result.draws}/{result.losses_a}"
        f"   win% (draw=½): {result.win_pct_a:.1%}",
        f"  mean VP margin (A−B): {result.mean_margin_a:+.2f}"
        f"   mean game length: {result.mean_turns:.1f} turns",
        "  think time (ms):",
    ]
    for name in (result.a_name, result.b_name):
        lines.append(
            f"    {name:10s} mean {result.think_mean_ms[name]:8.1f}  "
            f"p95 {result.think_p95_ms[name]:8.1f}  "
            f"max {result.think_max_ms[name]:8.1f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point (``uv run python -m app.ai.arena``)."""
    parser = argparse.ArgumentParser(description="Headless AI arena for hex-general.")
    parser.add_argument("--a", required=True, help=f"policy A ({sorted(REGISTRY)})")
    parser.add_argument("--b", required=True, help=f"policy B ({sorted(REGISTRY)})")
    parser.add_argument("--scenario", default="twin_rivers")
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--json", dest="json_out", default=None, help="write result JSON"
    )
    args = parser.parse_args(argv)

    result = run_match(
        args.a,
        args.b,
        args.scenario,
        args.games,
        args.seed,
        keep_results=args.json_out is not None,
    )
    print(_format_match(result))
    if args.json_out is not None:
        Path(args.json_out).write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
