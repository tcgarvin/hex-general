# AI Opponent Spec (v1)

Design for the first computer opponent for the hex wargame. This document is the
source of truth for the AI's algorithm, architecture, testing strategy, and
acceptance gates. Game rules live in `docs/SPEC.md`; this spec assumes them and
cites sections where a rule shapes the AI.

Status: **approved design, ready to implement.** Implementation phases are in §10.

---

## 1. Problem statement

Build an AI that can take a full planning-phase turn for one player slot:

```
plan(view: GameState, slot: PlayerId) -> (orders: list[Order], recruitments: list[Recruitment])
```

so that a human can play a complete game against the computer, and so we can run
headless AI-vs-AI games to playtest balance.

Properties of the game that constrain the approach:

1. **Simultaneous resolution** (SPEC §11). Both players plan, then orders execute
   together. This is a Markov game, not an alternating-turn game — standard
   alpha-beta / single-tree MCTS does not apply directly.
2. **Imperfect information** (SPEC §9). Fog of war hides enemy units outside
   vision range.
3. **Enormous joint action space.** Up to 20 units, each choosing Move (an exact
   path up to 4–5 hexes on a 40×40 map), Barrage, Rest, or Hold, plus
   recruitment. Per-unit candidates number in the hundreds; joint combinations
   are astronomical. Any approach must factor the action space per-unit.
4. **The engine is pure, deterministic, and importable.**
   `app.engine.execution.resolve_execution(state, orders, recruitments)` is a
   side-effect-free function. The AI can therefore *simulate candidate plans
   under the real rules* — pulses, ZOC stops, traffic jams, simultaneous damage,
   lead-entrant contests — instead of re-implementing an approximation of them.
5. **Speed budget is soft.** Play is asynchronous (plan → lock → resolve); an AI
   turn taking a couple of seconds server-side is acceptable. Millions of
   self-play episodes are *not* achievable against the pydantic-model Python
   engine, which rules out deep RL for this iteration (see §2.3).

## 2. Algorithm selection

### 2.1 Chosen approach: hierarchical heuristic planner with engine-simulation refinement

Three layers, all operating on the **fog-redacted view** (§5):

1. **Strategic layer** — score map objectives (settlements to take, own
   settlements to defend), assign units to objectives, and decide recruitment
   from simple economy rules.
2. **Tactical layer** — for each unit, generate a small candidate set (≤ ~8) of
   plausible orders: advance-toward-objective paths, attack moves, barrage
   targets, hold / rest / withdraw.
3. **Plan refinement** — start from each unit's top candidate, then run **greedy
   coordinate ascent**: repeatedly sweep the units, and for each unit try its
   alternative candidates while holding the rest of the plan fixed; score each
   variant by *simulating the turn with the real engine* against an opponent
   model (§4.4) and evaluating the resulting state with a static evaluation
   function (§4.3). Keep improvements; stop at a fixed simulation budget or
   convergence.

Why this wins for v1:

* Coordinate ascent over per-unit orders is the standard tractable move for
  factored simultaneous games; it finds coordinated plans
  (focus-fire, non-colliding paths) without enumerating the joint space.
* Using `resolve_execution` as the rollout makes the tricky rules *free*: the
  planner never needs its own model of movement pulses, ZOC-stop rules, traffic
  jams, or simultaneous-damage math — if a plan self-jams, the simulation shows
  the jam and the plan scores poorly.
* Every component is deterministic given a seed, so games replay exactly —
  matching the engine's own design philosophy and making tests stable.
* The design degrades gracefully: with a simulation budget of zero it reduces to
  the pure scripted bot, and each layer is independently testable.

### 2.2 Tiered policies (the ladder)

We build four policies. The lower tiers are not throwaway — they are the
measuring sticks that make "is the smart one actually smart?" an empirical
question (§7).

| Tier | Policy           | Description |
| ---- | ---------------- | ----------- |
| —    | `PassivePolicy`  | Every unit Holds. Floor for "does attacking beat doing nothing." |
| —    | `RandomPolicy`   | Seeded-random *legal* orders. Floor for "better than noise." |
| 0    | `ScriptedPolicy` | Strategic + tactical layers only, no simulation refinement (each unit takes its top candidate). Fast, deterministic, debuggable. |
| 1    | `SearchPolicy`   | Full design: Tier 0 plan + coordinate-ascent refinement with engine simulation. **The deliverable.** |

### 2.3 Approaches considered and rejected (for now)

* **Deep RL self-play (PPO / NFSP with per-unit policy factorization).** The
  right long-term answer for strength, wrong for now: it needs on the order of
  10⁶–10⁸ environment steps; the pydantic engine resolves a turn in
  milliseconds-to-tens-of-milliseconds (§8), giving perhaps 10⁴–10⁵ turns/hour
  per core — weeks of compute plus a re-implementation of the engine in a
  vectorizable form to be feasible. It would also produce an opaque bot exactly
  when the project's goal is *interpretable playtesting* of v0.1 rules. The
  `Policy` protocol (§4.1) is the seam where a learned policy plugs in later
  with no changes to the arena, tests, or service integration.
* **Simultaneous-move MCTS (DUCT / ISMCTS).** Sound in principle, but the
  branching factor after even aggressive per-unit action abstraction, multiplied
  by engine cost per node, makes tree search shallow to the point of
  uselessness here. The coordinate-ascent planner spends the same simulation
  budget on depth-1 plan quality, which is where this game's value is
  (a well-coordinated single turn), rather than on deep lookahead.
* **Exact per-turn optimization (ILP over unit-task assignment).** Adds a solver
  dependency and models only the assignment sub-problem; the interesting
  failures (jams, ZOC stops, simultaneous combat) live in execution, which the
  simulation-based refinement already covers.

### 2.4 "Training" in v1 = offline weight tuning, not gradient training

The evaluation function (§4.3) and strategic scoring have ~10–15 scalar weights.
v1 ships hand-set defaults. An optional tuning phase (§10, Phase 5) uses the
arena to hill-climb weights (random-restart local search or cross-entropy
method over weight vectors, fitness = win rate vs. the frozen default
`SearchPolicy`). This is cheap, reproducible, and requires no new dependencies.

## 3. Architecture

New package `api/app/ai/` (pure library — no HTTP, no DB, imports only
`app.engine.*`), tests in `api/tests/ai/`. Managed with uv like the rest of
`api/`; **no new runtime dependencies are expected or wanted.**

```
api/app/ai/
  __init__.py
  policy.py       # Policy protocol, BotPlan dataclass/model
  baselines.py    # PassivePolicy, RandomPolicy
  evaluation.py   # static evaluator: evaluate(state, slot) -> float, EvalWeights
  objectives.py   # strategic layer: objective scoring + unit-objective assignment
  candidates.py   # tactical layer: per-unit candidate order generation + pathfinding
  recruit.py      # recruitment rules
  scripted.py     # ScriptedPolicy (Tier 0) — composes objectives + candidates + recruit
  planner.py      # SearchPolicy (Tier 1) — coordinate ascent w/ engine simulation
  arena.py        # headless match runner (library + `python -m app.ai.arena` CLI)
```

Engine code is **not modified**, with one narrow exception allowed: if Phase 0
benchmarking (§8) shows `GameState.hex_at`-style linear scans dominating, the AI
builds its own coord-keyed dict indexes internally rather than touching the
engine. (The AI layer should do this anyway: build `{coord: hex}`,
`{coord: unit}` maps once per `plan()` call.)

Follow the repo's engine code standards (CLAUDE.md): trust internal invariants,
index dicts directly, fail loud; validate only at real boundaries. The AI's one
boundary is the `view` handed to `plan()` — and it arrives as an
already-validated `GameState`, so no re-validation is needed inside.

## 4. Component design

### 4.1 Policy interface

```python
class BotPlan(BaseModel):
    orders: list[Order]
    recruitments: list[Recruitment]

class Policy(Protocol):
    name: str
    def plan(self, view: GameState, slot: PlayerId) -> BotPlan: ...
```

* `view` is the **fog-redacted** state for `slot` (produced by the caller via
  `app.engine.fog.redact_state`). Policies never see the true state. This makes
  fog fairness structural rather than disciplinary (§5).
* Policies are **stateless across turns** in v1: each `plan()` call works from
  the current view only. (Cross-turn memory of last-seen enemies is explicitly
  deferred — it requires persistence in the service integration and is not
  needed to reach the acceptance gates.)
* Policies that use randomness (`RandomPolicy`, optional tie-break jitter) take
  a seed at construction and must be fully deterministic given
  (seed, view, slot).
* Every plan returned must be **statically legal**: it must pass
  `app.games.validation.validate_submission` for `slot` against the view. The
  arena asserts this on every turn of every game (§7.1). Note two rules
  validation does not check but the AI must respect by construction: barrage
  targets should contain a *visible enemy unit* (SPEC §7.2 — firing at empty or
  unseen hexes is legal but wasted), and recruitment onto an occupied city fails
  in the engine, so candidates must check `unit_at`.

### 4.2 Strategic layer (`objectives.py`)

1. **Objective list.** For the planning slot, enumerate:
   * *Capture objectives*: every settlement not controlled by us. Base value =
     victory points (city 3, town 1; SPEC §15.2) + income weight (city 3, town
     2; §6.2). Discounted by distance to our nearest eligible unit
     (`value / (1 + dist)`) and by observed defense (visible enemy units on or
     adjacent to it).
   * *Defense objectives*: every settlement we control with a visible enemy
     unit within its threat radius (enemy move range + 1). Value scales with
     settlement value and proximity of the threat.
   * *City-elimination awareness*: if we control exactly one city, defending it
     gets an overwhelming value (losing it loses the game, SPEC §15.1);
     symmetric logic boosts attacking an opponent's last city.
2. **Assignment.** Greedy: repeatedly take the (unit, objective) pair with the
   best `objective value / (1 + hex distance)` score until every combat unit has
   an objective; multiple units may share one objective (that is how focus-fire
   and escorts emerge). Artillery is assigned to *support* the nearest friendly
   cluster/objective rather than to capture (it cannot capture, SPEC §14.1).

### 4.3 Static evaluation function (`evaluation.py`)

`evaluate(state, slot) -> float`, weights in an `EvalWeights` model with tuned
defaults. Terms (own minus enemy where symmetric):

* **Victory override**: if `victory_status(state)` is decided, return ±BIG
  (win/loss) immediately.
* **Material**: Σ over units of `recruit_cost × hp / 10`. (Cost-weighting makes
  trading a 3-cost infantry for a 6-cost armor read as a win.)
* **Settlements**: Σ over controlled settlements of `Wcity=9 / Wtown=3`
  (VP-aligned 3:1, scaled up so a town ≈ an infantry unit).
* **Economy**: `bank × 0.1` (small; muster only matters if spent).
* **Position**: for each of our combat units, `−Wadv × dist(unit, its nearest
  capture objective)` — pushes the evaluator to prefer plans that end closer to
  objectives, which is what gives the one-turn simulation lookahead direction.
* **Exposure**: for each of our units, potential incoming damage next turn
  (Σ of the SPEC §13.1 damage formula over visible enemy units that could reach
  or barrage it), weighted small and negative; symmetric positive term for our
  potential damage onto visible enemies. This is the cheap stand-in for depth-2
  search.

When evaluating a *simulated* state during planning, the state was produced from
the redacted view, so "enemy" means *visible* enemy — acceptable and inherent
under fog.

### 4.4 Opponent model for simulation

When `SearchPolicy` simulates a candidate plan, the enemy's orders are modeled
as **all visible enemy units Hold**. Rationale: Hold is the defender-favorable
stationary baseline (they keep terrain + hold bonus, SPEC §7.4), so plans that
look good against it are conservative — attacks must beat dug-in defenders on
current terrain. This underestimates enemy maneuver but never hallucinates it;
it is deterministic; and it is the standard v1 choice. Predictive opponent
models are future work (§11).

### 4.5 Tactical candidate generation (`candidates.py`)

Pathfinding: A* / BFS on hexes with unit cost, impassable terrain excluded
(river without bridge, SPEC §3.4), occupied hexes excluded *except* an
enemy-occupied goal hex (attack-by-move, SPEC §7). Truncate paths to the unit's
move allowance; allow `move + 1` when every hex of the truncated path is road
(SPEC §3.3). Truncate at the first step that would violate the static ZOC rule
against the *visible, current* enemy positions (entering enemy ZOC is fine;
planning to continue ZOC→ZOC wastes the unit's turn, SPEC §8.4) — the engine
would stop the unit anyway; truncation just avoids predictably dead orders.

Per-unit candidates (cap ~8, deduplicated):

* **Advance**: truncated path toward the assigned objective (always included).
* **Attack moves**: paths ending in an enemy-occupied hex reachable this turn,
  for up to the 2–3 highest-value targets (value = expected net damage using
  SPEC §13.1, kill bonus, target cost).
* **Hold** (always included — it is also the no-op fallback).
* **Rest**: included when hp < max and no visible enemy within reach of the
  unit's hex (SPEC §7.3 — repair is wasted if damaged this turn).
* **Withdraw**: when in enemy ZOC and (hp low or locally outnumbered), a 1-step
  path to the best adjacent non-ZOC hex (SPEC §8.4 withdrawal).
* **Artillery**: Barrage candidates for each visible enemy unit in range
  (SPEC §7.2, range 3), scored by expected damage × target value with a kill
  bonus; plus reposition-toward-support and Hold. Artillery never generates
  attack-by-move candidates (SPEC §5.4).

### 4.6 Recruitment rules (`recruit.py`)

Rule-based, applied before order planning (recruits don't act this turn):

* Recruit while `bank ≥ cheapest cost` and under the unit cap, at controlled,
  unoccupied cities (nearest-to-front first).
* Composition targets a rough infantry-heavy mix (~50% infantry / 30% armor /
  20% artillery by count, weights tunable): pick the type whose current share is
  furthest below target *and* is affordable; prefer banking toward armor when
  bank + net income this turn ≥ armor cost and armor share is lowest.
* Never spend below a small reserve (default 0) — knob for tuning.
* Respect `validate_submission` recruitment rules exactly (affordability
  accumulates across multiple recruits, one recruit per city, unit cap).

### 4.7 Plan refinement (`planner.py`, `SearchPolicy`)

```
plan = top candidate per unit (ScriptedPolicy's plan)
best = score(plan)
for sweep in range(MAX_SWEEPS):            # default 3
    improved = False
    for unit in units (stable order, e.g. by unit id):
        for alt in candidates[unit] (excluding current):
            trial = plan with unit's order replaced by alt
            s = score(trial)
            if s > best: plan, best, improved = trial, s, True
            if sims_used >= SIM_BUDGET: return plan
    if not improved: break
return plan
```

`score(plan)` = `evaluate(resolve_execution(view, plan.orders + hold-orders for
visible enemies, plan.recruitments)[0], slot)`. Recruitments are fixed by §4.6
before refinement (they are cheap to get right by rule and interact weakly with
unit orders).

Budget defaults: `SIM_BUDGET = 150` simulations/turn, `MAX_SWEEPS = 3`.
Worst-case sims per sweep ≈ units × (candidates−1) ≈ 20 × 7 = 140, so the
budget typically allows one to two full sweeps — enough to fix jams and
coordinate focus-fire, which is the point. Both knobs are constructor
parameters.

## 5. Fog of war contract

* The policy input is always `redact_state(true_state, slot)`. In the arena the
  runner holds the true state and redacts per side each turn; in service
  integration (§9) the service does the same. There is **no code path that
  hands a policy the true state.**
* The planner may therefore reason only about visible enemies. Hidden-enemy
  effects (ambushes, unseen garrisons) simply happen to the AI, as they do to a
  human. No cheating, and no pretend-uncertainty modeling in v1.

## 6. Arena (`arena.py`)

Headless runner, both a library API and a CLI (`uv run python -m app.ai.arena`).

* `play_game(scenario_state, policies: dict[PlayerId, Policy], *, seed, max_turns_guard) -> GameResult`:
  loop { redact per slot → `plan()` → assert `validate_submission` passes →
  `resolve_execution` → `victory_status` } until decided (the engine's turn
  limit guarantees termination; the guard is a crash-loud backstop, not a
  silent truncation).
* `GameResult`: winner/draw, reason (city elimination / turn-limit VP), final
  VP per side, turns played, units lost per side, per-turn plan wall-clock
  stats, and the full per-turn order log (for debugging/replays).
* `run_match(policy_a, policy_b, scenario, n_games, base_seed)`: alternates
  colors each game (the scenario is asymmetric — each pairing must play both
  slots equally), varies seeds, aggregates W/D/L, mean VP margin, mean game
  length, mean/max think time.
* CLI: `--a search --b scripted --scenario twin_rivers --games 20 --seed 7
  [--json out.json]`, printing an aggregate table. Policy names resolve through
  a small registry in `arena.py`.

The arena is both the acceptance-gate harness (§7.3), the balance-playtest tool,
and the fitness function for weight tuning (§2.4).

## 7. Testing strategy

All tests are pytest under `api/tests/ai/`, run by `uv run pytest` with the rest
of the suite. The four repo checks (pytest, black, ruff, mypy) gate every phase.

### 7.1 Invariants (enforced everywhere, not just in tests)

* **Legality**: every `BotPlan` from every policy passes `validate_submission`.
  The arena asserts this on every turn; a violation is a crash, not a warning.
* **Determinism**: same (policies, scenario, seed) → byte-identical `GameResult`
  order logs. Tested by running a short match twice and comparing.

### 7.2 Unit / behavioral tests

Small hand-built states via the existing engine test helpers
(`api/tests/engine/helpers.py`), not the 40×40 scenario:

* *Evaluation*: winning material/settlement positions score higher; decided
  victory dominates everything; symmetry (`evaluate(s, blue) ≈ −evaluate(s,
  red)` on mirrored terms).
* *Candidates*: paths are contiguous/passable/allowance-truncated; road +1
  applies only to all-road paths; ZOC truncation; artillery only barrages
  hexes containing visible enemies in range; no attack-by-move for artillery;
  rest only offered when safe and damaged.
* *Objectives*: undefended nearby city outranks defended distant town; last-city
  defense dominates.
* *Recruitment*: spends legally (cap, bank accumulation, occupied city skipped),
  converges toward the target mix over turns.
* *Behavioral micro-scenarios* (assert on the chosen plan, tolerant to exact
  paths):
  1. Lone enemy-held town, superior local force → plan attacks/moves on it.
  2. Artillery + spotter vs. adjacent enemy stack → artillery barrages the
     highest-value target rather than moving.
  3. Badly wounded unit in enemy ZOC with a safe hex available → withdraws or
     is at least not ordered ZOC→ZOC.
  4. Two units whose naive shortest paths cross → refined plan does not
     self-jam (simulate the plan; assert no `TrafficJam` event among own units).
  5. Rich bank + free city → recruits.

### 7.3 Acceptance gates (arena, statistical)

Win% counts a draw as half. Colors alternate. Scenario: `twin_rivers`.

| Matchup                        | Games | Gate |
| ------------------------------ | ----- | ---- |
| `ScriptedPolicy` vs `RandomPolicy`  | 20 | ≥ 90% |
| `SearchPolicy` vs `RandomPolicy`    | 20 | ≥ 95% |
| `SearchPolicy` vs `PassivePolicy`   | 20 | ≥ 95% |
| `SearchPolicy` vs `ScriptedPolicy`  | 30 | ≥ 65% |
| `SearchPolicy` think time           | —  | p95 ≤ 5s/turn, mean ≤ 2s/turn on dev hw |

The full gates run via a pytest marker (`-m arena`, excluded from default runs)
and via the CLI; CI runs a **smoke subset** (4 games per matchup, gate relaxed
to "smart side does not lose the majority") to keep pipeline time sane. Gate
results get recorded in the implementation report with exact seeds so they are
reproducible.

### 7.4 What we deliberately do not test

Engine correctness (owned by `api/tests/engine/`), and exact plan outputs on the
full scenario (brittle; behavioral assertions only).

## 8. Performance plan

**Phase 0 measurement before any planner code:** benchmark
`resolve_execution` on `twin_rivers` turn-0 with ~16 move orders, and
`GameState.model_copy` cost, using a scratch script (report numbers in the
implementation report; a scratch script is fine here, it does not ship).

Decision rule:

* Full-turn simulation cost `t_sim ≤ 20 ms` → design as specced
  (150 sims ≈ ≤ 3 s worst case). Expected path.
* `20 ms < t_sim ≤ 60 ms` → drop `SIM_BUDGET` to 50 and `MAX_SWEEPS` to 2;
  candidates per unit to 5.
* `t_sim > 60 ms` → refinement scores fall back to **static scoring**
  (evaluator on a movement-only projection: apply the unit's path optimistically
  with no combat, plus §4.3 exposure terms) and only the final best-of-3 whole
  plans get one true simulation each. This keeps the architecture intact while
  we optimize later.

AI-side micro-rules regardless: build coord-keyed dicts once per `plan()`;
never call `GameState.hex_at`/`unit_at` in loops; reuse the candidate list
across refinement sweeps (generate once per turn).

## 9. Service integration ("vs. bot" games)

Minimal API-side integration so a human can actually play the bot (frontend
button/UX is **out of scope** for this spec; the existing client can pass a
flag later):

* `POST /games` gains an optional `vs_bot: bool = false` (and optional
  `bot_policy: str = "search"` resolved via the arena registry). When true, the
  service creates the game, fills the second slot with a bot player record
  (no user, unusable token), and the game starts immediately.
* In `submit_orders`, when the opponent slot is a bot and the human's orders are
  locked: compute `redact_state(state, bot_slot)` → `policy.plan(...)` →
  `validate_submission` → store as the bot's submission → resolve the turn as
  usual. Synchronous is fine at ≤ 5 s think time; it happens inside the existing
  lock/resolve flow.
* The bot's plan is validated by the same path as a human submission — the
  service must not grow a trust shortcut for bot orders.
* Tests: service-level test that a vs-bot game can be created, played several
  turns by submitting human orders, and reaches resolution; bot never blocks a
  turn; resign works. Reuse the existing service test fixtures.
* After this phase ships, regenerate the frontend client types
  (`npm run gen:api`) since the request schema changed — even though no frontend
  UI is built yet, the generated `schema.d.ts` must not drift.

## 10. Implementation phases

Each phase ends with the four checks green (`uv run pytest`, `black --check`,
`ruff check`, `mypy`) and a commit on this worktree branch.

* **Phase 0 — benchmark + scaffolding.** Perf numbers per §8 and the §8
  decision recorded; package skeleton; `Policy`/`BotPlan`; `PassivePolicy`,
  `RandomPolicy`; arena `play_game`/`run_match` + CLI; determinism + legality
  invariant tests. (Baselines-vs-baselines games already run here.)
* **Phase 1 — evaluation + strategic layer.** `evaluation.py`, `objectives.py`,
  unit tests.
* **Phase 2 — tactical layer + scripted bot.** `candidates.py`, `recruit.py`,
  `scripted.py`; behavioral micro-tests; gate: Scripted ≥ 90% vs Random.
* **Phase 3 — search policy.** `planner.py`; anti-self-jam test; full arena
  gates of §7.3; record gate results + seeds.
* **Phase 4 — service integration.** §9, including the `gen:api` regeneration.
* **Phase 5 (optional, separate decision) — weight tuning.** §2.4 tuner script +
  tuned `EvalWeights` defaults, only if Phase 3 gates pass but play quality
  disappoints.

## 11. Future work (explicitly out of scope)

Cross-turn memory of last-seen enemies; predictive opponent models; multi-turn
lookahead; deep RL self-play behind the same `Policy` seam (would need a fast
engine re-implementation); difficulty levels (trivially: sim budget / weight
noise); frontend vs-bot UX; bot-vs-bot balance dashboards.
