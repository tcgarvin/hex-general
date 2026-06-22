# Hex Wargame v0.1 Development Roadmap

A phased plan to implement [`SPEC.md`](./SPEC.md). The deploy stack
(Vercel → Cloud Run → Supabase) already works; the scaffold ships a trivial
`games` table and `/stats`. Everything below is the actual game.

**Player model for v0.1:** async two-browser play. Each player submits orders
from their own client; the server executes once both have locked in. This makes
player identity, order ownership, and **server-enforced fog of war** first-class
concerns rather than afterthoughts.

---

## Foundational architecture decisions

Settle these before Phase 1 — they shape every phase.

1. **The engine is a pure function.** `resolve_turn(state, orders) -> (new_state, event_log)`
   — no I/O, no DB, no randomness. Everything in §11–14 lives here. The hardest
   part of the spec becomes unit-testable headless, and replay falls out for free
   (the event log *is* the replay).
2. **Game state is JSONB in one row, not a wide relational schema.** A `games`
   row holds the canonical state blob (map + units + turn number + banks). Don't
   model units/hexes as tables — the state is small (40×40, ≤40 units) and churns
   every turn; relational modeling fights the pure-engine design. Keep `players`
   and `turns` (order submissions + resolved event logs) as real tables for
   ownership/history.
3. **Axial hex coordinates** (`q, r`) with cube conversion for distance/LOS. Pick
   this once; it's load-bearing for movement, range, and vision.
4. **Fog is enforced server-side.** The API computes each player's visible set and
   returns a *filtered* state. The browser never receives hidden units (matches
   the CLAUDE.md rule that the browser only sees what the API hands it).
5. **Town/City are terrain types, not overlays.** A settlement hex *is* town/city
   terrain (SPEC §4.1/§4.2 lists them with their own +2 defense); there is no base
   terrain underneath. The only dynamic fact is ownership, so `Settlement` is a
   thin control record (`coord`, `controlled_by`) and the kind is read from the
   map terrain — one source of truth. Road stays a boolean layer on `Hex`.

---

## Phase 0 — Domain model & game state (no rules yet) ✅ done

**Goal:** a typed, serializable representation of everything in §3–6.

- ✅ Pydantic models in `api/app/engine/models.py`: `Hex` + `Terrain` enum,
  `Settlement` (control overlay), `Unit` (type, hp, owner, position), `Player`
  (bank; control derived from settlements), `GameState`, `Order` union
  (Move/Barrage/Rest/Hold). Reference stat tables in `engine/stats.py`.
- ✅ Hex coordinate library (`engine/hex_coords.py`): axial + cube, neighbors,
  distance, with tests.
- ✅ JSON (de)serialization round-trip tests (`tests/engine/`).

**Demonstrable:** load a hand-written state, serialize, reload — identical.
(`tests/engine/test_models.py::test_game_state_json_round_trip`.)

## Phase 1 — Map & scenario ✅ done

**Goal:** §3, §4, §15 victory data exist as concrete content.

- ✅ A scenario file format (JSON) defining the map: terrain and roads as two
  parallel character grids (`.wh~=tc` / `#`), plus settlement ownership, starting
  units per player, player roster, and victory config (`api/app/engine/scenario.py`).
- ✅ Map loader + validation: `load_scenario` / `load_scenario_file` build a turn-0
  `GameState`, raising `ScenarioError` with context on grid-geometry, river/bridge/road
  (§3.4), settlement-terrain, unit-placement (passable, unstacked §8.1), and ownership
  problems. Settlement records are derived from town/city terrain (single source of
  truth); the scenario supplies only ownership.
- ✅ One real 40×40 starter scenario authored (`api/app/scenarios/twin_rivers.json`):
  a river split with three bridged road corridors, a city+town per side, two neutral
  contested towns, and starting units.

**Demonstrable:** load scenario → valid `GameState`
(`tests/engine/test_scenario.py::test_starter_scenario_loads_and_is_self_consistent`).

## Phase 2 — Execution engine: movement (the riskiest core) ✅ done

**Goal:** §11.2, §8, §7.1 — movement pulses, no combat yet.

- ✅ Pure `resolve_movement(state, orders) -> (new_state, events)`
  (`api/app/engine/movement.py`): pulse loop, exact-path following with no
  reroute, road +1 movement (§3.3, from the full planned path), river/bridge
  passability (§3.4), ZOC stop-on-entry (§8.3), stacking (§8.1), friendly
  traffic jams (§8.2). Simultaneous within-pulse resolution settles
  follow-the-leader chains via a fixpoint.
- ✅ Serializable, discriminated `Event` log (`api/app/engine/events.py`):
  `UnitMoved`, `MoveBlocked` (reason enum), `TrafficJam`, `ZoneOfControlStop` —
  the replay; combat events join this union in Phase 3.
- ✅ Test-first (`tests/engine/test_movement.py`): per-mechanic tests plus
  whole-event-log golden traces of multi-unit scenarios.

**Seams to Phase 3 (documented in `movement.py`):** running into an enemy stops
the unit with no damage and a `MoveBlocked(reason=enemy|contested)` marking where
combat will resolve. True cycles (2-unit swaps, pure rotations) can't resolve
without stacking, so those units stay put — a v0.1 limitation; enemy swaps are
§12.9 combat.

**Demonstrable:** golden-file tests of multi-unit movement scenarios
(`tests/engine/test_movement.py`).

## Phase 3 — Execution engine: combat & barrage ✅ done

**Goal:** §12, §13, §7.2 — the heart of the spec.

- ✅ During this phase the spec's movement/ZOC/combat model was rewritten
  (SPEC §5.4, §7–8, §11–12, §16–17, §19): **ZOC restricts movement but never
  causes combat** (engaged units may only attack, hold, or withdraw — no ZOC→ZOC
  move, §8.4), and **combat no longer ends movement** — a surviving lead entrant
  continues along its path, so breakthroughs emerge with no armor-only rule
  (§11.2, §12.5).
- ✅ Pure combat math (`api/app/engine/combat.py`): formula (§13.1) clamped 1..5
  with a single HP-scaling hook for §13.2 (deferred to P10); simultaneous damage
  where every unit hits every opponent (a lone defender retaliates against all
  attackers, §12.4); terrain + Hold modifiers on the stationary defender only
  (§12.10); lead-entrant tie-break and occupation (§12.5–12.6, §12.8).
- ✅ Barrage phase (`api/app/engine/barrage.py`): resolves pre-movement against
  the start-of-phase occupant, terrain/Hold mods, no retaliation, destroyed units
  removed before the first pulse (§7.2, §11.3); range/type are boundary-validated
  (`EngineError`).
- ✅ Movement reworked (`api/app/engine/movement.py`) to the new ZOC model with
  combat interleaved per pulse — into-enemy attacks, empty-hex contests, edge
  swaps (§12.9), and continuation — plus `resolve_execution`
  (`api/app/engine/execution.py`) composing **barrage → movement+combat**.
- ✅ Rich `CombatResolved`/`BarrageResolved` events carry the §16.3 explanation
  data; `MoveBlockedReason` gained `ZOC_LOCKED`/`ATTACK_FORBIDDEN`.

**Demonstrable:** exhaustive combat/barrage/execution unit tests including the
nasty cases — dogpiles, lead-entrant selection, three-way contests, edge swaps,
breakthrough continuation (`api/tests/engine/test_combat.py`, `test_barrage.py`,
`test_execution.py`).

> **Documented v0.1 simplifications** (rare simultaneity corners, see
> `movement.py`): an attacked occupant is pinned to defend; a third unit hitting
> a hex already in an edge swap is blocked rather than merged; pure friendly
> cycles still deadlock in place.

## Phase 4 — Execution engine: end-of-turn phases ✅ done

**Goal:** §11.3 phases 3–6 + victory.

- ✅ Capture (`api/app/engine/capture.py`): infantry/armor occupying a town/city
  flips control; artillery cannot capture and empty settlements are never
  abandoned (§14.1–14.2). Runs before economy so a fresh capture earns income.
- ✅ Repair (`api/app/engine/repair.py`): undamaged resting units heal +1-up-to-7
  anywhere or +2-up-to-10 on a settlement (§7.3); "up to" is a ceiling, never a
  reduction. Damage taken this turn (the caller's diff over execution) cancels repair.
- ✅ Economy (`api/app/engine/economy.py`): per-player `income - upkeep` (§6.2–6.4),
  bank floored at 0 on a shortfall and clamped to the cap with overflow reported
  as `lost_to_cap`.
- ✅ Recruitment (`api/app/engine/recruitment.py`): a new `Recruitment` input deploys
  a unit at an empty friendly **city**, validating cost, the unit cap (§6.1), and
  placement and raising `EngineError` on any violation (§6.5); applied in order so
  each request spends the running bank.
- ✅ Victory (`api/app/engine/victory.py`): pure `victory_status` query — city
  elimination first (§15.1), then turn-limit settlement scoring (§15.2), with ties
  as draws.
- ✅ `resolve_execution` now composes all six phases (barrage → movement+combat →
  capture → repair → economy → recruitment) and advances the turn; `Event` gained
  `SettlementCaptured`/`UnitRepaired`/`EconomyResolved`/`UnitRecruited`.

**Milestone — headless playable game ✅:** a test drives a full game start→victory
with no UI (`tests/engine/test_execution.py::test_headless_game_drives_to_city_elimination_victory`).
The spec's central question is now *mechanically* answered.

## Phase 5 — Turn lifecycle API & persistence (async two-player) ✅ done

**Goal:** wire the engine to the deployed stack with real identity.

- ✅ Tables (`supabase/migrations/`): `games` extended with the canonical `state`
  JSONB blob + `scenario`/`status` lifecycle; `players` (opaque token ↔ scenario
  slot), `turn_orders` (one locked submission per player per turn), resolved
  `turns` (event log + state + victory). Each new table enables RLS and grants
  DML to `service_role` (the GRANT gotcha).
- ✅ Player identity: lightweight opaque bearer tokens (`secrets.token_urlsafe`)
  presented as `X-Player-Token`; a token resolves to one game slot and can only
  act on its own game (`app/games/identity.py`, `routes.require_player`).
- ✅ Endpoints (`app/games/routes.py`): `POST /games` (create + claim first slot),
  `POST /games/{id}/join` (claim the open slot, activate when full),
  `GET /games/{id}` (full view — fog is Phase 6), `POST /games/{id}/orders`
  (validate + lock; resolve synchronously when **both** are in),
  `GET /games/{id}/turns/{turn}` (replay).
- ✅ Server-side order validation at the boundary (`app/games/validation.py`):
  ownership, one-order-per-unit, contiguous/on-map/passable paths, artillery +
  in-range barrage, recruit cost/cap/controlled-city — the engine stays pure and
  trusts its input. Lifecycle orchestration is `GameService` over a `GameRepo`
  protocol (`app/games/service.py`, `repo.py`), unit-testable via an in-memory
  fake.

**Demonstrable:** a full two-player game over HTTP — create → join → submit →
execute → replay → turn-limit victory — driven through the real FastAPI app
(`tests/games/test_routes.py`, `test_service.py`).

## Phase 6 — Fog of war (server-enforced)

**Goal:** §9 — visibility before any rendering exists.

- LOS with woods blocking (§9.2), vision radius per unit (§9.3), per-player
  visible-hex computation.
- The "fetch my view" endpoint returns filtered state + filtered event log (you
  only see combat you can witness).

**Demonstrable:** tests proving hidden enemy units never appear in a player's
payload.

## Phase 7 — Frontend: map & state rendering

**Goal:** §16, read-only first.

- Hex grid render (terrain, settlements, roads, rivers/bridges), your units, fog.
  Consume the filtered view from Phase 6.

**Demonstrable:** look at your board in the browser.

## Phase 8 — Frontend: planning UI

**Goal:** §16.1.

- Select unit → movement range → assign exact path; assign Barrage/Rest/Hold;
  predicted pulses; own-unit traffic-conflict warnings; review-all + lock.
- Submits to Phase 5 endpoints.

**Demonstrable:** plan a real turn end-to-end in the browser.

## Phase 9 — Frontend: replay

**Goal:** §16.2–16.3.

- Step-through of the event log: moves, blocks, traffic jams, combat with the
  §16.3 explanation text, destructions, captures, economy delta.

**Milestone — first full playable match in two browsers.**

## Phase 10 — Tuning, scenario polish, success criteria

**Goal:** §18 / §13.2.

- Playtest, tune the prototype stat tables, optionally trial HP scaling (§13.2),
  author a scenario that produces §18's situations (road feints, woods
  uncertainty, bridge chokepoints).

---

## Why this order

The engine (Phases 2→4) is both the risk and the value, and it's pure/headless,
so the rules can be validated completely before touching pixels. Fog (Phase 6)
lands before rendering so the frontend is built against already-filtered data and
can never accidentally leak hidden units. Each phase has a demonstrable artifact,
and there are three real milestones:

- **P4 — headless-playable** (engine drives a full game in tests)
- **P5 — API-playable** (full game via curl, two players)
- **P9 — browser-playable** (first full match in two browsers)

## Open decision: auth depth

Phase 5 is scoped to lightweight tokens (enough to own orders). Real accounts
would be a small insert between P5 and P9 — but the spec's "no live-service"
framing suggests keeping it minimal for v0.1.
