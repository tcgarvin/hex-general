# AI Opponent Spec (v1)

Design for the first computer opponent for the hex wargame. This document is the
source of truth for the AI's algorithm, architecture, testing strategy, and
acceptance gates. Game rules live in `docs/SPEC.md`; this spec assumes them and
cites sections where a rule shapes the AI.

Status: **approved design, ready to implement.** Implementation phases are in §10.

> **Retired 2026-11-10:** §§1-11 describe the 2-player bots (the scripted "Blitz" and
> search "Sage" policies, the evaluation function, the plan-refinement planner, the
> arena harness and its acceptance gates, and the vs-bot service integration). They
> were deleted with the 2-player game (`app/ai/scripted.py`, `planner.py`,
> `evaluation.py`, `baselines.py`, `arena.py` and their tests; `objectives.py` is cut
> down to the `Objective` types). Kept as history. Still in the tree and used by the
> world bots: `app/ai/policy.py`, `board.py`, `candidates.py`, `recruit.py`,
> `objectives.py` and `base_plan.py`. **§12 (world bots) is current.**

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

---

## 12. World addendum (WORLD_SPEC §8, 2026-11-09)

World bots live in `app/ai/world.py`; the world simulator in `app/world/sim.py`.
The v0.1 policies above were retired with the 2-player game on 2026-11-10; the
world bots reuse only the shared pieces listed at the top of this doc.

### 12.1 Two layers, one seam

A world bot's turn is split where the later LLM commander plugs in (VISION §9):

- **Strategist** (`Strategist` protocol): reads the fog-redacted view, returns
  `StrategistArgs`: a `stance` (expand, attack, defend), an optional `focus` hex, a
  `reserve_share` kept home as garrisons, an `aggression` weight, and an optional
  `operation` (below). `ScriptedStrategist` is the slice-1 stand-in. When it runs an
  operation, the stance steers the units outside it. Without one it defends when
  another player's unit is within 8 of a holding and the bot is cautious (neutral
  garrisons never move, so they never threaten). It expands while a *cheap* neutral
  site lies within its `reach`: one whose garrison tier HP is at most 30 for the most
  cautious bot and 5 for the boldest, so bold bots turn on their neighbours sooner. It
  also keeps expanding until it has a field army of five combat units. Otherwise it
  attacks the hostile-held settlement nearest its holdings.
- **Operation** (`app/ai/operations.py`, added 2026-11-11): one offensive as plain
  data: `target` player, `objective` (a settlement the target holds), `staging` (where
  the force assembles: the own holding nearest the objective if within 8, else the hex
  5 short of it on the straight line from that holding), `force` (combat units committed) and `assemble`
  (how many must stand ready before it advances). An LLM strategist fills the same
  fields; `BotPlan.operation` reports it so the simulator can measure it.
- **Tactical layer** (`plan_world_turn(view, slot, args)`): writes a legal `BotPlan`.
  An LLM strategist replaces only the first layer.

Each bot has a seeded `Personality` (aggression, reach, garrison share), so a world of
bots isn't a world of clones.

**How the scripted strategist picks an operation** (`plan_operation`). Nothing
survives between ticks on the live server: `GamesService` builds a fresh `WorldPolicy`
each tick, and no player field or table holds bot memory. So the operation is derived
from the view alone and made sticky by what the view does carry over: where the bot's
units stand, and its own standing travel orders (private to it, kept between turns).

- *When:* a war of choice starts once the bot has no cheap neutral land in reach and
  its army reaches its war size, from 12 combat units for the most cautious bot to 8
  for the boldest. While land is cheap the only war is against a *clearly beaten*
  neighbour (the bot holds twice its settlements and at least 3 more). No operation
  runs while two hostile units stand within 6 of the bot's base, or with fewer than 3
  free combat units.
- *Commitment:* a war is under way at a hostile settlement when 2 or more own combat
  units stand within 7 of it, or march to a staging area near it. Such a war goes on
  with an army 3 below the war size, and each engaged unit adds 3 hexes of preference
  for that target and objective, so the more of the army is committed, the harder it
  turns. There is no explicit horizon: an operation ends when its objective falls (the
  next one of the same target follows), the target leaves reach, the army drops below
  the band, or home is in danger.
- *Whom and where:* the nearest neighbour holding a settlement within `reach` + 6,
  preferring one already fought, clearly beaten, or with units on the bot's land. The
  objective is that neighbour's settlement nearest the bot's holdings, or its base once
  clearly beaten.
- *How much:* 60% of the free combat units for the most cautious bot up to 90% for the
  boldest (40% of that while finishing a neighbour during expansion), all of them when
  hostile units stand within 6 of the staging area. The force must reach 1.2 times the
  target's units in view within 3 of the objective; an objective the free army cannot
  match is passed over for the next, and with none left the bot runs no operation.

### 12.2 Tactical layer

Rewritten 2026-11-10/11 (`app/ai/world.py`, with `app/ai/intel.py` and
`app/ai/base_plan.py`).

- **Intel** (`Intel`) is built once per turn from the redacted view, inside a box around
  the bot's units and focus. It holds the visible player units (threats) and every
  garrisoned neutral site in the box. A site within 3 of a visible neutral unit is
  *seen* and counted at the units' real HP; an unseen one is counted at its tier's full
  HP (the tier is on the public map). It also marks *danger spots*, the hexes where a
  column would meet a garrison and auto-deploy disordered.
- **Targets** are settlements. A neutral site is worth its value (by kind and by what the
  bot lacks, below) ÷ (1 + 0.15 × garrison HP) and gets a siege group sized to the
  garrison by `siege_group`: 2 units for a militia post, 3 for tier 2, 4 for tier 3 and 5
  for tier 4, with a smaller minimum. A hostile-held settlement is valued by its guessed
  defence and needs at least two units. Own holdings near a visible player unit become
  defend targets sized to the threat.
- **Groups** (`_match_groups`): targets are filled best first, each scored by value ÷
  (1 + d ÷ 4), where d is the distance of the nearest free unit that would complete its
  minimum group. A target takes its nearest units up to capacity, so units are matched
  as whole groups, never one at a time. Units already at a target (within 3), and
  columns still marching to it, stay with it.
- **Staging:** a group that is not yet gathered waits about 6 hexes out, on ground where
  it can rest, and strikes when its minimum has arrived. Against a player a unit attacks
  only with a second striker on the same enemy, or to finish an enemy at 3 HP or less,
  or to clear an enemy next to an own holding.
- **Attack scoring** (`candidates.py`): an attack that the return fire would kill the
  attacker on, without killing the target, carries a large penalty; attacks and barrages
  on the group's objective carry a bonus, and attacks on garrisons far from it are not
  offered. Artillery stands off 2 hexes and barrages the besieged site.
- **Rest:** a unit at 4 HP or less leaves the fight for the nearest settlement ground
  within 8 (+3 HP a turn there) and returns at 8 HP. It rests in place when no such
  ground is in reach.
- **Travel:** a column travels straight to its goal when no garrison lies near the way.
  Otherwise its route is cut at the first danger spot, so it stops short and goes on
  deployed rather than marching into contact. Columns bound for a held settlement still
  stop 5 short. A column deploys on purpose within 6 of a player unit.
- **Front reserve:** a unit with no target joins the front (the own holding nearest a
  hostile one) and waits there in support.
- **Garrisons:** `reserve_share` of the combat units, rounded down, stay home, so a small
  army keeps nobody home. A unit standing on the recruit hex steps off so the recruit can
  appear.
- **Economy** (`base_plan.py`): barracks 2 comes before the workshop. A fuel-burning unit
  is never recruited into a fuel deficit; manpower may run at a loss only while the stock
  covers 20 turns of it. Site needs follow the flow and the stock: a farm is worth 2.5×
  while manpower runs at a loss and 0.5× while the stock is near full; a steel mill is
  worth more the more turns short of the next building's steel the bot is; a second
  factory is worth little.
- **Operations** (`_Campaign`, 2026-11-11): the force is the operation's `force`
  healthy free combat units nearest the staging area or objective; units already in
  the zone (staging distance + 3 around the objective) keep their place, and a worn
  unit drops out to rest and is replaced by the next nearest. Outside the zone a unit
  makes for the staging area. The force *gathers* there until `assemble` units stand
  ready in the zone (deployed, 6 HP or more), then *advances*. Every visible hostile
  unit in the zone is the battle; the *focus* is the one most ready units (and guns in
  range) could hit this turn. The force *strikes* when enough of them can reach the
  focus (the assembly size less one, at least 2) at 1.2:1 local odds counted within 3
  of it; then every striker hits the focus, and in the zone nothing else is struck
  except a nearly dead unit or one beside an own holding. Short of that the force
  closes up on the focus but never ends adjacent to an enemy. While gathering it hits
  back at an enemy that comes at the staging area or into contact, with 2 or more
  units. Guns assigned to the operation stand off and barrage as elsewhere. No other
  group is sent at the objective.
- **Tactical units** (a hostile within 6) reuse the v0.1 candidates (§4.5). Moves claim
  the hexes they enter per pulse and their final hex, so a bot never jams itself; attack
  steps are not claimed, so several units can strike one target.

### 12.3 Simulator and gates

`uv run python -m app.world.sim --slots 12 --seed 1 --turns 60 [--json out]`, or
`--seeds 20` for the WORLD_SPEC §9 gate tally. Every plan is validated before it resolves.
The engagement size is the mean deployed units per side in each cluster of front units
(units within two hexes joined), per turn.

Added 2026-11-10: neutral and player captures; siege turns (first attack on a garrison to
capture) and siege attackers (distinct attackers per besieged site); units lost to
garrisons; attacks on player units and the share made with local superiority (more of
the attacker's deployed units than the defender's within 3); and the worst single
bot-turn think time.

Added 2026-11-11 (`_War`): attacks at 2:1 local odds or better; assault sizes (distinct
own units striking one target area, strikes within 2 hexes chained, in one turn, guns
firing into it included); each bot's share of its units in contact per turn, over all
bot-turns and over bot-turns with any unit in contact; operations started, succeeded
(the bot holds the objective or the target is gone when the operation changes),
abandoned, or open at the end, by `(target, objective)`; turns from an operation's
start to its first strike on the target; and who took each eliminated player's last
settlement. A one-turn blip in the operation counts as an abandon and a new start, so
the operation counts overstate churn.

### 12.4 Measurements and known gaps

20 seeds of 12-slot worlds, 60 turns (2026-11-09): travel ends in arrival or contact
20/20, jams only near contact 20/20, resolution under 5 s 20/20; every bot at 3
settlements by turn 20 only 4/20; every neighbour pair fought by turn 30 0/20 (strict:
counted from combat, so a quiet border or an early elimination fails it); median
engagement of 3 a side 0/20.

100 bots on the 200-slot world (seed 1, 60 turns, 7 min on one core): resolution 0.8 s
median and 2.5 s worst, bot planning 27 ms mean and 1.2 s worst. Every bot held 3
settlements by turn 20, and none was eliminated. Holdings at turn 60 ran 1–41
(median 18). Half-filled worlds have room to expand; the crowded 12-slot test worlds
don't.

**World economy (2026-11-10), 100 bots on the 200-slot world, seed 1, 60 turns (66 s):**
resolution 0.18 s median and 0.82 s worst with ~2,500 garrison units; planning 4 ms
mean. Bots build and recruit with `app/ai/base_plan.py` and value sites by kind and
need. Median units 5 at turn 14, 7 at 28, 8 at 42, 9 at 60 (8 of 100 reach 15);
holdings including the base 3 / 4 / 5 / 7. That is about half the economy draft's
model player (11 units at turn 28, 15 by turn 45), which is fine while bots only fill
the map; better bots wait for the user's first playtest. 31 units disbanded by
shortages in 60 turns across all bots. Bots attack a garrison with one unit at a time,
which is slow; massing on a site was the first bot improvement to make (done, below).

**Bot rework (2026-11-10/11).** The same runs before and after the §12.2 rewrite.

20 seeds of 12-slot worlds, 60 turns (medians across seeds of each seed's median bot):

| | before | after |
|---|---|---|
| every bot at 3 holdings by turn 20 | 1/20 | 8/20 |
| neighbours fought by turn 30 | 0/20 (112 of 447 pairs) | 0/20 (155 of 447 pairs) |
| median engagement of 3 a side | 0/20 | 0/20 |
| travel, jams, resolution gates | 20/20 each | 20/20 each |
| units at turn 14 / 28 / 42 / 60 | 5 / 5.5 / 6 / 6.5 | 6 / 9 / 11 / 12 |
| holdings at turn 14 / 28 / 42 / 60 | 2.5 / 3.25 / 4 / 4 | 3.5 / 5 / 6 / 6.5 |
| eliminations / shortage disbands | 17 / 102 | 6 / 124 |
| neutral / player captures | 862 / 211 | 1,332 / 331 |
| attackers per besieged site (mean) | 1.42 | 1.84 |
| units lost to garrisons | 268 | 11 |
| attacks on players with local superiority | 39% | 52% |

100 bots on the 200-slot world, seed 1, 60 turns, each run alone on one core:

| | before | after |
|---|---|---|
| units at turn 14 / 28 / 42 / 60 | 5 / 6 / 7 / 8 | 6 / 9 / 12 / 15 |
| holdings at turn 14 / 28 / 42 / 60 | 3 / 4 / 5 / 6 | 4 / 7 / 9 / 11 |
| eliminations / shortage disbands | 0 / 26 | 0 / 17 |
| median engagement a side | 1.0 | 1.0 |
| neutral / player captures | 561 / 66 | 1,034 / 105 |
| units lost to garrisons | 192 | 7 |
| attacks with local superiority | 42% | 54% |
| neighbour pairs in contact by turn 30 | 11 of 104 | 18 of 104 |
| think time per bot-turn, mean / worst | 16 ms / 1.19 s | 30 ms / 0.16 s |
| resolution, worst turn | 2.8 s | 4.3 s |
| wall time | 264 s | 395 s |

On the big world the median bot now tracks the economy draft's model player loosely:
9 units at turn 28 (model 11) and 12 at turn 42 (model 15 by 45). Planning stays well
inside the 2 s budget; the old 1.2 s worst case was the full-map site scans that the
intel box removed. Resolution is slower because there are more units and more fights.

**Operations (2026-11-11).** Round 2 is about war between players. Baselines were re-run
on the round-1 bots with the new sim metrics and matched round 1's numbers exactly.

20 seeds of 12-slot worlds, 60 turns (shares are over all fight clusters or attacks of
the 20 runs; units and holdings are medians across seeds of each seed's median bot):

| | round 1 | operations |
|---|---|---|
| fight clusters with 2+ a side / 3+ a side | 16.8% / 1.9% | 24.7% / 5.5% |
| fight clusters one against one | 60% | 53% |
| median engagement of 3 a side (gate) | 0/20 | 0/20 |
| assaults by 2+ units / 3+ units (one area, one turn) | 27.6% / 6.3% | 34.0% / 10.4% |
| attacks on players with local superiority / at 2:1 | 51% / 36.5% | 49.7% / 33.1% |
| front share, all bot-turns / bot-turns in contact | 5.5% / 17.6% | 6.2% / 19.2% |
| operations started / succeeded / abandoned / open | none | 670 / 121 / 416 / 133 |
| turns from an operation's start to its first strike (median) | none | 2 |
| neighbour pairs fought by turn 30 | 155 of 447 | 144 of 447 |
| eliminations (all by another player) | 6 | 13 |
| neutral / player captures | 1,332 / 331 | 1,235 / 255 |
| units lost to garrisons / shortage disbands | 11 / 124 | 12 / 164 |
| every bot at 3 holdings by turn 20 | 8/20 | 8/20 |
| units at turn 14 / 28 / 42 / 60 | 6 / 9 / 11 / 12 | 6 / 9 / 10.25 / 11.75 |
| holdings at turn 14 / 28 / 42 / 60 | 3.5 / 5 / 6 / 6.5 | 3.5 / 5 / 6 / 6 |
| think time per bot-turn, mean / worst | 26 ms / 0.84 s | 21 ms / 0.82 s |

100 bots on the 200-slot world, seed 1, 60 turns, each run alone (the machine was shared
with other work, so times are noisy):

| | round 1 | operations |
|---|---|---|
| fight clusters with 2+ a side / 3+ a side | 14.8% / 1.3% | 14.9% / 3.4% |
| assaults by 2+ units / 3+ units | 25.5% / 5.6% | 24.9% / 5.9% |
| attacks on players with local superiority / at 2:1 | 54% / 43% | 51% / 41% |
| front share, all bot-turns / bot-turns in contact | 2.3% / 12.6% | 2.7% / 13.5% |
| operations started / succeeded / abandoned / open | none | 192 / 25 / 117 / 50 |
| neighbour pairs in contact by turn 30 | 18 of 104 | 17 of 104 |
| eliminations | 0 | 1 |
| neutral / player captures | 1,034 / 105 | 982 / 98 |
| units lost to garrisons / shortage disbands | 7 / 17 | 4 / 35 |
| units and holdings at turn 14 / 28 / 42 / 60 | 6 / 9 / 12 / 15 and 4 / 7 / 9 / 11 | the same |
| think time per bot-turn, mean / worst | 31 ms / 0.13 s | 36 ms / 2.3 s |
| wall time | 404 s | 418 s |

The half-filled big world has cheap land in reach for most bots all game, so wars of
choice rarely start and little changes. The 2.3 s worst bot-turn is a garbage-collection
pause, not planning: re-running the slow bot-turns under a profiler took 65 to 83 ms,
and on a rerun the one slow bot-turn spent 0.91 of its 0.93 s in the collector (the
world state is a large heap, and a full collection lands wherever the allocation count
trips it). `gc.freeze()` after loading the state, or a higher generation-2 threshold in
the tick, would keep such pauses out of the 2 s budget.

What the numbers say. Fights are bigger: twice the share of clusters with two or more a
side, nearly three times with three, and more assaults by three or more units. Wars are
finished: twice the eliminations, every one by a neighbour. Operations commit: on
seeds 1 and 3 traced turn by turn, wars start between turns 17 and 40 and most bots
hold one target for 10 to 40 turns (a few flicker on and off as the army hovers at its
war size), and the force strikes about two turns after an operation starts. Growth holds within a few percent of
round 1 (neutral captures down 7%, holdings at turn 60 down half a settlement) only
because wars of choice wait until cheap land is gone. Two things got worse: player
captures (down 23%) and attacks at 2:1 odds (down 3 points). The median engagement is
still one a side, so the §9 fronts gate still fails.

What was tried, on the same 20 seeds:

- *Wars of choice at the border while expanding* (a hostile settlement within 8 to 10
  of the bot's holdings, 40% to 100% of the free army): clusters with 2+ a side rose to
  29% to 38% and 3+ to 6% to 10%, but neutral captures fell 20% to 29% (950 to 1,074),
  player captures fell to 221 to 258, and holdings at turn 60 to 4.75 to 5.5. The force
  is taken out of expansion, and wars kill units (25% to 50% more player units died on
  six instrumented seeds). The shipped setting is the conservative end of this trade.
- *Waiting for the whole force* (assemble = force less one): forces sat for 25 turns at
  the staging area while a lone enemy wore units down. Assembling at 60% fixed it.
- *Strike discipline without odds*: the force struck as soon as enough units were in
  reach, into equal numbers; requiring 1.2:1 within 3 of the focus raised attacks at 2:1
  from 25% to 30% at a small cost in cluster size.
- *Closing up on the objective* left the force short of a focus standing off to the
  side: only 10% of operation turns struck, 60% were spent closing up. Closing up on the
  focus raised strikes to about 25%.
- *Force membership by distance alone* swapped units in and out of the force every turn,
  marching them back and forth; units in the zone now keep their place.
- *A flat stickiness bonus* let a bot flip between two neighbours it was engaged with;
  stickiness per engaged unit cut that. *Counting every march as engagement* started
  wars by accident (expansion columns near a neighbour's land); only marches that look
  like staging count now.
- *Preferring settlements seen empty* (an expected-defence term in the objective score)
  made objectives flicker as units came into and out of view; dropped.
- *Starting wars only when the neighbour's units stand on the bot's land* churned the
  same way (units flicker in and out of view); dropped.

Gaps:

- Player captures fell. Round 1's came mostly from groups of two walking into empty
  hostile sites. An operation goes for the neighbour's settlement nearest the bot, which
  is usually where the neighbour's army stands, and the 1-damage combat floor makes that
  slow: on one traced war a force of four needed 13 turns of fighting to take an oil
  field its neighbour kept feeding defenders into. Choosing weakly held objectives needs
  intel that persists (a remembered last sighting), not the current view, which
  flickers.
- Combat math (a tuning observation, not acted on): infantry against infantry deals 1
  damage both ways in nearly every case (attack 4 against defence 5 clamps to the floor),
  and in a dogpile every attacker takes the defender's full return blow. A massed attack
  therefore trades HP evenly and wins only by killing sooner. Concentration pays less
  than players will expect, which caps how much better massed bot attacks can measure.
- No explicit commitment horizon. The live tick keeps nothing between turns, so an
  operation lasts as long as its derivation stays put (units engaged, standing orders).
  A 28-turn horizon, and abandoning an operation that has made no progress in N turns,
  need a per-bot notebook: a small JSON field on the player row, written by the tick and
  read by the strategist (VISION §9). That needs a migration and was not built.
- Fronts and defence (goal 3) are unchanged from round 1 apart from the all-in response
  when the enemy stands at the staging area: no line on defensible terrain, no mobile
  reserve behind it. A losing bot does not consolidate beyond dropping its operation.
- The operation counts in the sim overstate churn: a one-turn blip (a visible raider, an
  army one unit short) closes an operation and starts another.
- Neighbours fought by turn 30 stays 0/20 (144 of 447 pairs): wars of choice wait for
  the land to run out, which in 12-slot worlds is after turn 30 for most bots.
- Shortage disbands rose 32% (164 against 124), following the extra farm losses to
  other players, as in round 1.

Gaps before 2026-11-11 (round 1):

- Fights stay small: the median engagement is still one unit a side in both worlds.
  About 63% of front clusters are one against one, mostly two moving units meeting on the
  way somewhere. Group attacks on a lone defender measure about two a side. Neither
  holding attack groups back nor withdrawing lone units from contact moved the median.
  Bigger fights probably need fronts that stay put (borders that bots hold), which is a
  strategist job, or a world denser than the 12-slot test maps where every bot spends
  its first 20 turns on garrisons.
- Neighbours fought by turn 30 fails on the strict count: the 12-slot worlds still have
  neutral land between most bots at turn 30, and a bot keeps expanding while cheap
  neutral land is in reach.
- Shortage disbands in the 12-slot worlds rose with the fighting: nearly all of them
  (33 of 36 traced on three seeds) follow the loss of a farm to another player, when
  manpower income drops below upkeep with an empty stock. Recruiting only into a
  manpower surplus, or keeping a manpower reserve of 8, did not reduce them. Per player
  capture they fell from 0.48 to 0.37.
- Steel binds the build order: at turn 20 most bots sit at their army size cap with
  24 to 30 manpower (the storage limit) and 16 or less steel, earning 1 to 4 a turn,
  against barracks costs of 13 and 23 (a tuning question, not a bot one).
- About a quarter of unit-turns change objective, mostly when new intel re-values a
  target. Columns stop short of unseen garrisons, which costs time; routing around them
  through a waypoint did not help and raised disbands.
- No scouting, so columns still meet hidden enemies; disorder is the price.
