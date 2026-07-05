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
6. **The frontend is a Vite SPA, not Next.js.** The app is a client-rendered Pixi
   canvas talking to the external FastAPI API — Next's SSR/RSC/server-actions/
   image-opt go unused, and SSR actively fights Pixi. Stack: Vite + React + TS +
   Tailwind v4, client-side routing for lobby/board/replay, deployed to Vercel as
   a static build. (Replaces the create-next-app scaffold; chosen while the
   frontend was still empty, so no app code was lost.)
7. **The board renders in PixiJS with AI-generated pixel art.** A WebGL/Canvas 2D
   scene via `@pixi/react` v8 (declarative, React 19); no SSR workarounds needed
   on Vite. Sprites are AI-generated as a fixed-style, fixed-dimension set;
   **player colour comes from Pixi tinting one base unit sprite** (not per-colour
   art) and damage is an overlay — keeps the generated set small and consistent.
   Fog/ZOC/range/path overlays are Pixi graphics layers above the tile layer.
8. **The client learns turn progress by polling**, not Supabase Realtime.
   `GET /games/{id}` on an interval; when `turn`/`locked` advances, fetch the
   replay. One data path (the REST API), no RLS/subscription surface in v0.1.
9. **Frontend types are generated from the API.** FastAPI's OpenAPI schema is the
   single source of truth for `GameState`/`Order`/event shapes; TS types are
   codegen'd so the churning engine can't drift from the client.

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

## Phase 6 — Fog of war (server-enforced) ✅ done

**Goal:** §9 — visibility before any rendering exists.

- ✅ LOS with woods blocking (§9.2) via hex line-drawing (`hex_coords.line`),
  vision radius per unit (§9.3), and per-player visible-hex computation as pure
  queries (`api/app/engine/visibility.py`): `has_line_of_sight`,
  `unit_visible_hexes`, `visible_hexes`. Binary visibility (§9.1) — the observer
  sees into an adjacent/unobstructed woods hex but not through it.
- ✅ Redaction (`api/app/engine/fog.py`): `redact_state` drops enemy units on
  hexes the viewer can't see (own units always kept); `filter_events` keeps only
  events the viewer could witness — combat/movement on visible hexes plus anything
  involving their own units, with per-player economy private to its owner.
- ✅ The "fetch my view" endpoints apply fog: `GET /games/{id}` returns the
  redacted state, `GET /games/{id}/turns/{turn}` returns redacted state + filtered
  event log. Victory stays computed from the authoritative full state.
- **v0.1 scope (documented):** only enemy *units* are secret; the static map,
  settlement control, and player banks are public. Replay visibility is computed
  from the post-turn state (a unit that fled into the fog mid-turn isn't
  reconstructed).

**Demonstrable:** hidden enemy units never appear in a player's payload — unit
tests (`tests/engine/test_visibility.py`, `test_fog.py`) plus HTTP integration
(`tests/games/test_routes.py::test_view_hides_enemy_units_in_the_fog`,
`::test_replay_hides_enemy_units_and_keeps_economy_private`).

## Phase 7 — Frontend foundation: lobby, accounts & local dev ✅ done

**Goal:** an authenticated, live-updating game context for every later phase to
build on, plus a working laptop playtest loop. **Re-scaffolds the frontend as a
Vite SPA** (decision 6) — replacing the create-next-app boilerplate — adds
**lightweight SSO accounts** (resolves the auth-depth decision below), and stands
up **local Supabase** so a dev can play a full match offline. See
[`FRONTEND.md`](./FRONTEND.md) for personas, journeys, and page structure.

**Progress — Phase 7 ✅ done.** Backend slice shipped & live; Slice 2 (Vite SPA)
shipped; Slice 3 (local stack + seed) shipped. The
API validates the Supabase JWT (ES256 via the project JWKS) at the boundary and
identifies callers by `user_id`; `players` gained `user_id` and the opaque
`token`/`X-Player-Token` retired; `games` gained an `invite_token`; create/join
enforce **one active game per user** and invite-gated joins; `GET /games/active` is
the dispatcher; `GET /me` reports the caller's id + dev role; and the dev role +
`X-Dev-Profile` impersonation (below) is enforced. The frontend is re-scaffolded as a
**Vite + React + TS + Tailwind v4 SPA** (React Router) with Supabase-Auth/Google
login, a typed API client generated from the OpenAPI schema, the dispatcher /
join / game-shell routes with a polling-driven live `GameView`, and the dev profile
switcher. Slice 3 ships the `supabase start` local stack + seed for offline
laptop playtesting: `supabase/config.toml` configures local ES256 JWT signing
keys (so the API's JWKS auth boundary is unchanged), `api/app/dev_seed.py` seeds
the `@hex.local` dev accounts, and `scripts/dev.sh up`/`down` bring the stack
up/down and wire the env files (per-worktree isolated). See
[`LOCAL_DEV.md`](./LOCAL_DEV.md).

- **Vite + React + TS + Tailwind v4 SPA** with client-side routing (login /
  dispatcher / join / game shell); `frontend.yml` builds a static bundle, deployed
  to Vercel. Env vars move from `NEXT_PUBLIC_` to `VITE_`.
- **Accounts via Supabase Auth (Google SSO).** The frontend holds the Supabase
  session; the API **validates the Supabase JWT** to identify the user (`Authorization:
  Bearer`). `players` gains `user_id`; the opaque `X-Player-Token` retires to an
  internal detail. **One active game per user** — login dispatches straight to it.
- **Auth-aware typed API client:** TS types **generated from FastAPI's OpenAPI
  schema** (single source of truth for `GameState`/`Order`/events), requests carry
  the Supabase JWT.
- **Dispatcher + onboarding:** after login, a user with an active player row lands
  in their game; otherwise the create / paste-join screen. A shareable invite link
  (game id + unguessable invite token) opens to log in → claim the open slot.
- **Polling hook** on `GET /games/{id}` surfacing `turn`/`status`/`locked[slot]`; an
  advance triggers a replay fetch. Game-context provider exposes the current
  `GameView`, your slot, and lock state to the tree; the **game shell** renders
  your-move vs waiting-on-them and is where later phases mount board/planning/replay.
- **Dev profile switcher**: a corner overlay listing seeded identities; pick one →
  act as that user, for solo two-sided playtesting. Works **on the deployed stack
  too**, gated by a *very lightweight* role system — a user is a dev iff their id is
  in the API's `DEV_USER_IDS` allowlist (no role table), and a dev (only a dev) may
  impersonate another user via the `X-Dev-Profile` header (403 otherwise). Locally,
  the same switcher rides on a seeded dev's local-Auth session. (This revises the
  earlier "deployed API rejects the dev path" plan — see Resolved: auth depth.)
- **Local dev playtesting:** `supabase start` runs the full local stack (Postgres +
  Auth) from `supabase/config.toml` + migrations; the local API and Vite dev server
  point at it via env; a seed creates dev users (+ optional starter game). Real
  Google is configured only on deployed envs — **local playtesting uses dev profiles
  against local Auth**, so no OAuth setup is needed to play on a laptop.

**Demonstrable:** two identities (dev profiles locally, or two Google logins on the
deploy) create/join one game and each sees its `GameView` (turn, slots, lock state)
refresh live as the other acts — no board yet — and a dev can run the whole loop on
a laptop via `supabase start`.

## Phase 8 — Frontend: map & state rendering (PixiJS pixel-art)

**Goal:** §16, read-only.

- PixiJS scene (`@pixi/react`) laying hexes out from axial `q,r`, pan/zoom with
  integer scaling for crisp pixels; per-hex hit-testing (clicks → `q,r`) so
  Phase 9 has its input surface.
- Terrain/settlement/road/river/bridge tiles + your units, consuming the
  fog-filtered Phase 6 view (hidden enemy units are simply absent from the payload).
- AI-generated sprite set (fixed style/dimensions); player colour via Pixi tint,
  damage as an overlay.

**Demonstrable:** your real board in the browser — terrain, settlements, your
units, fog.

### Tileset maturation

Base map rendering shipped with one AI-generated flat-top hex tile per terrain
(`frontend/src/board/tiles/`, generated via PixelLab `create_tiles_pro` hex mode
at 64px — see Slice T0 and the `CLAUDE.md` memory note; originally 32px). These
slices grow that from "one tile per terrain" toward a varied, directional tileset.

**2026-07-01 update:** plains, woods, hills, city, and town have since been
replaced by the painterly tile-foundry pipeline (128px, concept-art register) —
the pipeline contract and terrain classes are documented in
`docs/ART_PIPELINE.md`, with full provenance in `art/foundry/`. The slices below
still describe the remaining oriented work (rivers/bridges/roads), which will
follow the same pipeline.

**Orientation decision (applies to rivers/bridges/road-tiles):** no 60° rotation
— rotating 64px pixel art by 60° resamples and looks mushy. Generate the distinct
oriented tiles instead, but use a cheap **horizontal (left↔right) mirror** at
render time to roughly halve the art needed per shape.

**Variation discipline (applies to every multi-variant terrain):** variants of a
terrain exist to break up visible tiling repetition with *mild* differences —
same terrain, slightly different grass/rock/canopy noise. They are **not** for
introducing distinct features: no forest-with-a-clearing, no plains-with-a-pond,
no hills-with-a-ruin. A player should read all variants of a terrain as "the same
thing, rendered a little differently," never as different map content. When
generating/picking variants, reject any candidate that adds a recognizable feature
and keep only subtle reshufflings of the base texture.

**Plains art direction — calm via smoothness, NOT paleness:** plains are the
highest-frequency, lowest-information hex; they should read as calm open ground
and *recede*, not compete for the eye. Achieve that through **smoothness / low
internal detail** (no busy clutter, no features) while keeping the green
**saturated and mid-toned** — a confident, warm, slightly-yellow daylight grass
like the concept art. Two failure modes, both seen and rejected: (1) *too busy* —
high-frequency texture/high contrast that distracts; (2) *washed out* — draining
saturation and pushing lightness to "recede," which yields a pale pastel/grey-green
that looks faded and lifeless. "Low contrast" means **low internal detail**, never
**low saturation**. Target a saturated mid-green (mean luminance ~110–120), not a
pale one (~130+).

**Judge tiles at board scale + check luminance parity (every terrain):** a montage
at full tile size lies. The board auto-fits the map, so hexes render small (~50px),
and a **dark/dense** tile collapses into a muddy "rocky" speckle that reads as a
*different material* — this is exactly how a too-dark woods batch (mean luminance
~88 against plains ~130) showed up as grey "rock" on the board. So: (a) compute
each variant's **mean luminance** (opaque pixels) and keep terrains in a sensible
relative band — woods only *modestly* darker than plains, not ~35% darker; (b)
always ratify on the **actual rendered board** (`verify-board`), not the montage
alone. Brightness relationships between terrains matter as much as each tile in
isolation.

- **Slice T0 — 64px resolution upgrade.** Tiles authored at 32px looked
  low-detail; moved to 64px (~4× the art data, 2× zoom-in headroom before
  pixelation). Goal was crispness only, *not* larger on-screen tiles — the board
  auto-fits the map, so apparent size is held constant.
  - **T0a (geometry, code only) ✅** — `HEX_SIZE` 16→32 (`hex.ts`) so the 64px
    texture blits 1:1; the sprite is already `HEX_SIZE*2`. In `Board.tsx`, halved
    the absolute zoom clamps (`MIN/MAX/INITIAL_MAX_ZOOM`) to keep fit/zoom
    behaviour identical, and doubled the world-space cosmetics that don't key off
    `HEX_SIZE` (font sizes, stroke widths, ring/path insets, marker radii).
  - **T0b (art regen) ✅** — regenerated every terrain at 64px so the tiles
    blit at native resolution instead of upscaling the old 32px art 2×. Pure
    asset work — `import.meta.glob` discovers the PNGs, so **no code edit**;
    independent of T3–T5 (their edge-mask orientation logic is px-agnostic) and
    doable one terrain at a time, each independently shippable. All 15 tiles
    (plains/woods/hills ×3, city ×3, river/bridge/town ×1) replaced with 64px art
    from PixelLab `create_tiles_pro` hex/top-down batches; verified rendering on
    the local board (crisp, cohesive, no upscale blur).

    **Recipe (PixelLab MCP):** `create_tiles_pro`, `tile_type:"hex"` (flat-top),
    `tile_view:"top-down"` (flat, no extruded depth), **`tile_size: 64`**. The
    tool returns ~16 numbered variations per call; poll `get_tiles_pro` (~2 min)
    and pick the best per terrain. See the `hex-general-terrain-tiles` memory note
    for the full recipe and gotchas.

    **Targets — regenerate to match today's variant layout**
    (`tiles/{terrain}/{nn}.png`, sorted by filename, indexed by
    `variantIndex(q,r,count)`):
    `plains` ×3, `woods` ×3, `hills` ×3, `city` ×3, `river` ×1, `bridge` ×1,
    `town` ×1. (Variant counts can grow later as a T2-style drop-in; T0b just
    re-renders the current set at 64px.) When picking the `city` variants,
    preserve the earlier rubble fix (city `00` was regenerated once to remove a
    noisy rubble artifact — don't reintroduce it).

    **Caveats:** at 64px the busier tiles cross Vite's 4 KB `assetsInlineLimit`,
    so expect a mix — some inlined as base64, some emitted as `dist/assets/*.png`.
    Verify a build by confirming the tiles **render** (browser/board), not by
    counting `data:image/png;base64` in the bundle. No scenario/format change.

- **Slice T1 — Variant infrastructure.** Loader returns `Record<Terrain,
  Texture[]>`; tiles live in `tiles/{terrain}/{nn}.png`, discovered by
  `import.meta.glob` (drop-in: no code edit to add a variant). A hex picks its
  variant deterministically from its axial coord (`variantIndex(q, r, count)`),
  so the choice is stable across re-renders/replays and purely cosmetic.
- **Slice T2 — Variant art for common terrains.** Generate 3–4 interchangeable
  variants each for plains/woods/hills (the high-frequency hexes where tiling
  repetition reads worst) and regenerate the noisy `city` tile. Pure asset work
  on top of T1.
- **Slice T3 — Rivers as edge-connected grassland features.** River = grassland
  base + river feature (kept on grass only, to cap combinations). Derive each
  river hex's edge mask from its river/bridge neighbours (`AXIAL_DIRECTIONS` →
  the 6 flat-top edges) and classify into a small shape set: end (1), straight
  (2 opposite), gentle curve, sharp curve, confluence (3+); a couple of curve
  radii give "a few different curves". Generate distinct oriented tiles + L↔R
  mirror per the orientation decision. No scenario-format change — connectivity
  is derived from existing `~` placement.
  - **T3a (straight) ✅ shipped.** Mask-driven render in
    `frontend/src/board/riverTiles.ts` (`connectedEdges` + `classifyRiver`):
    straight = 2 opposite edges → `straight_v` (vertical, self-mirror) or
    `straight_d` (one generated "\" diagonal + render-time L↔R mirror for "/").
    Any unhandled mask (ends, turns, forks) falls back to the generic
    `tiles/{river,bridge}/00.png`, so a horizontal `~` line still renders (it's
    all 60° *turns*, T3b). **Matching is exact by a deterministic edge-snap, not
    by generation:** PixelLab generates the river interior, then
    `scripts/river-edge-snap.py` forces each connected edge's crossing to a
    canonical position/width/colour at the edge centre (its `verify`/`match`
    subcommands assert seams align). `twin_rivers` gained a short straight
    vertical tributary + a straight bridge to exercise it.
  - **T3b-turns (gentle turns + diagonal straight) ✅ shipped.** Painterly
    tile-foundry cuts (see `art/foundry/manifest.json` river `sources`): NBP
    zigzag/diag guide swatches cut at 864px (6.75:1) for width parity with
    `straight_v`. Four generated turn tiles (`turn_n_se`, `turn_s_nw`,
    `turn_se_sw`, `turn_ne_nw`) + L↔R mirror cover all six 120°-apart edge
    pairs; `straight_d` ×3 replaced the legacy PixelLab tile. `twin_rivers`'
    horizontal river (all gentle turns) now renders continuously.
  - **T3b-forks (tributary junction) ✅ shipped.** Two coupled pieces: (1)
    `riverTiles.ts` now derives a *course graph* — raw adjacency over-connects
    junctions (the hexes around a tributary mouth are mutually adjacent), so a
    penalty-minimising prune drops junction-triangle chord edges (never
    disconnecting a river) and the junction cluster resolves to one true fork +
    already-shipped turn/straight shapes. (2) One generated fork tile
    `fork_se_sw_s` (SE→SW course, S branch; mirror-symmetric) via the foundry's
    new `fork` guide kind (zigzag-h peaks each dropping a straight S tributary)
    — see the manifest river `sources`. twin_rivers' namesake junction now
    renders fully painterly.
  - **T3b-rest (other forks/ends/sharp turns) — forks + ends ✅ shipped 2026-07-03; sharp 60° turns disallowed.**
    The three ratified fork families landed as 9 new generated shapes / 12
    tiles via the foundry's all-straight junction-tree guide kinds
    (`branch-v` / `branch-d1` / `branch-d2` / `y` / `fans-b`) — straight-through
    + 60° branch, the symmetric 120° Y ({0,2,4}/{1,3,5}, self-mirror), and the
    consecutive-edge fans — see the manifest river `sources`. With render-time
    L↔R mirrors and the already-shipped `fork_se_sw_s`, **17 of the 20 three-edge
    masks now have real art**; all 20 are mapped in `riverTiles.ts`. The fans-a
    pair (`fork_se_ne_n`, `fork_ne_n_nw`, covering masks {0,1,2}/{2,3,4} and
    {1,2,3}) is **parked**: across 5 rolls NBP always paints a downstream outlet
    through the dead-end fan junction (an all-arms-upward junction is
    hydrologically outlet-free), so those two shapes need a guide redesign if a
    map ever demands them. **Ends ✅ shipped 2026-07-03:** four generated end-cap
    tiles (`end_n` / `end_s` / `end_ne` / `end_se`) via the foundry's tapered
    `ends` guide kind — each a spring head that, per user direction, snakes into
    a very thin trickle petering out past the hex centre (the first wedge/arrow
    round was rejected and re-rolled against a revised guide). With the render-time
    L↔R mirror **all six 1-edge masks now have real art**; see the manifest river
    `sources`. **Sharp 60° turns (2 adjacent edges) are disallowed outright (user,
    2026-07-03):** map authoring must not create them, the course pruner penalizes
    them, the `00` single-tile fallback stays defensive-only, and they will never
    get art. (A scenario-loader lint that rejects sharp-turn courses is possible
    future work, out of scope here.) **Ratified junction inventory (user,
    2026-07-02): those three fork families only** — straight+60° branch,
    symmetric 120° Y, and fans (fans are forced whenever a N/S tributary meets a
    horizontal course, since an E–W course crosses a hex via 120°-apart edges).
    No 4-arm junctions: the course pruner dissolves them. None of these fork
    tiles occur in twin_rivers after course pruning — generated on demand when a
    map needs them.
- **Slice T4 — Bridges as oriented river crossings.** Bridge orientation = the
  river axis it crosses (derivable from its two river edges); reuse T3's
  edge-mask machinery and the mirror approach. **Straight bridges ✅ shipped with
  T3a** (`bridge/straight_v`, `bridge/straight_d`); they reuse the river edge
  mask + edge-snap. **Painterly turn bridges ✅ shipped 2026-07-03:**
  `bridge/turn_se_sw` + `bridge/turn_ne_nw` (stone deck) via the foundry's new
  deck-composite route (`scripts/tile-foundry/bridge.py`): an NBP deck sprite +
  baked road ramps + contact shadow composited over the ratified river tiles, so
  water/grass stay byte-identical and seam-compat is inherited. Rulings: bridge
  decks (and structures generally) get a *little* 3/4 perspective like the woods
  trees — strictly-top-down decks were rejected as unreadable, and perspective
  forbids sprite rotation (horizontal and vertical decks are generated natively);
  a placement gate in `bridge.py` asserts the deck covers every water pixel on
  the crossing line. Painterly bridge tiles bake their ramps, so `Board.tsx`
  suppresses the hex's own vector road stubs when the resolved bridge texture is
  128px (legacy 64px bridge art keeps them). **Not ratified:** the painterly
  `straight_v` bridge (vertical water, road bending se↔sw over the deck) — the
  road-bend composition read poorly; out of scope for now, twin_rivers' (4,20)
  keeps the legacy `straight_v` tile. Legacy `straight_d` deleted (unused by any
  map; regenerate through the foundry on demand). Fork bridges likewise on
  demand.
- **Slice T5 — Road rendering ✅ shipped 2026-07-04 (baked tiles win).** The
  experiment compared, on the same map, today's **vector overlay** (centre-to-edge
  brown lines, `Board.tsx`) vs **baked road tiles** autotiled by road-edge mask
  (reuses T3 machinery). Verdict: **baked NBP-finished road tiles win** on visual
  cohesion — the vector overlay reads as a flat ribbon pasted over the terrain,
  while a baked tile wears the road *into* the ground and grades it over hill
  relief. The vector overlay is **kept as the fallback**: a road hex renders its
  baked tile only when one exists for its `(terrain, shape)` pair
  (`byOriented["road/{terrain}_{shape}"]`, `roadTiles.ts`), so partial art
  coverage never breaks a network's continuity. Pipeline (`scripts/tile-foundry/`
  `roadfinish.py`): deterministic composite (`road.py` bakes the flat 12px ribbon
  onto the ratified plains/hills bases) → **NBP image-to-image finish** (wear/grade
  the road) → deterministic post-chain (road-mask recolour back to the ribbon
  palette + b08 rim blend) that tames NBP's width/colour freelancing and pins the
  grassy rim + edge crossings to the deterministic input, so tiles still abut
  invisibly. Auto-gated (drift/width/rim/lum, +phantom for ends); the user ratified
  the per-slot picks off board strips. **Shipped: 24 tiles — plains fully covered
  (18: straights ×3, turns ×2, ends ×1 each), hills 6** (straight_v — a
  20.5px-core width-waived near-miss ratified from the board strip, gate 20.0 —
  plus straight_d, turn_s_nw, turn_se_sw, end_se, end_n). The **four remaining
  hills shapes** (`turn_n_se`, `turn_ne_nw`, `end_ne`, `end_s`) are deliberately
  left to the vector fallback — NBP kept over-widening the road over their steeper
  relief. Settlements out of scope. **Reproducibility:** the 2K raw NBP gens
  (~210MB) are *not* committed; the committed per-tile source is the 128px
  post-resize intermediate (`art/foundry/road/gens128/`, ~0.5MB total), from which
  the deterministic post-chain byte-replays (`roadfinish.py build --verify`, wired
  into `selftest.py`; regenerate the sources with `export-sources`).

### Unit art (painterly sprites)

Replace the vector unit markers (`UnitMarker` in `Board.tsx`: tinted
circle/square/triangle + centred HP digit) with painterly sprites in the
concept-art register. Design ratified with the user 2026-07-05:

- **Register: WWII, no national insignia** — infantry = a small 2–3 soldier
  squad, armor = one mid-century medium tank, artillery = a towed field gun
  (crew optional; offer both in candidates). Team colour does the faction work.
  The concept art's units are illustrative-only for *content*, but its mood/
  palette register applies.
- **Team colour: two-layer sprite (base + tint mask)** — must scale to **32+
  player colours** (user requirement), which rules out baked per-player files
  and whole-sprite multiply tint. Generate each unit once with its team areas
  (tunics/helmets, hull panels, gun carriage — the areas the concept art
  team-tints) painted in a keyable colour; the foundry classifies those pixels
  by hue (the hills/river HSV machinery) and emits a neutral **base** PNG plus
  a luminance-preserving **mask** PNG that Pixi tints with `playerColor` at
  render time. Metal, wood, and skin stay natural in the base.
- **HP: 10-segment pip bar** (SPEC max HP 10) replaces the centred digit —
  the concept art's pip-row look. Exact geometry decided at integration; must
  stay legible at ~50px board-scale hexes.
- **Perspective/facing:** the standing "a LITTLE 3/4 perspective" ruling
  (woods trees, bridge decks) applies; strictly top-down was already rejected
  as unreadable. Units have no gameplay facing — one generated facing
  (west), optional render-time L↔R mirror for variety. No rotation.
- **Asset class 5 — free-standing sprites** (new foundry class, easiest of
  the five: no seams, no edge anchoring, no cut-off risk). White-background
  candidate sheet via NBP (`gen.py`) → segment by connected components +
  un-matte + contrast-compress toward the concept register (the
  `vignette.py`/tree-sprite treatment) → mask cut + gates (footprint band,
  luminance band, mask-coverage sanity) in a new manifest-driven
  `scripts/tile-foundry/unit.py` with `--verify` byte-reproduction, wired
  into `selftest.py`. Provenance under `art/foundry/units/`.
- **Sizing:** ~75–95px footprint on a 128×128 transparent canvas (the vector
  marker is ~62% of hex width today), soft ground-shadow disc like the woods
  trees, strong silhouette — terrain was deliberately made to recede so units
  pop.
- **Integration (code):** sprites live outside the terrain glob (e.g.
  `frontend/src/board/units/` assets + a small loader beside `tiles.ts`);
  `UnitMarker`/`RecruitGhost` render base sprite + tinted mask sprite + pip
  bar; slide/fade replay animation and order badges unchanged. No API or
  scenario change.
- **Ratification:** per the CLAUDE.md hard gate — montage + board-scale
  strips over plains/woods/hills/city, published as a claude.ai Artifact;
  the user picks. Slices: **U1** candidate sheets + picks (art),
  **U2** `unit.py` foundry pipeline (base/mask split + gates), **U3**
  renderer integration (two-layer sprites + pip bar).

**✅ Shipped 2026-07-05 (U1+U2+U3).** Four NBP candidate rounds → user picks:
armor = v3-r1 tank, artillery = v3-r2 gun+two-crew, infantry = v4 5-man wedge
at 0.9× footprint (90/90/81px bbox on the 128 canvas — infantry is drawn at
**footprint parity** with the tank, NOT realistic soldier scale, which fixed
both "reads small" and pixelation). `scripts/tile-foundry/unit.py`
(manifest-driven, `build --verify` byte-clean, selftest-wired) splits each
crop into `frontend/src/board/units/{type}_{base,mask}.png`; `units.ts` +
`UnitMarker`/`RecruitGhost` render base + owner-tinted mask + a 10-pip HP
bar, vector markers kept as the pre-load fallback. Verified on the live
local board (both armies, tint clean, pips legible). Rulings that transfer:
- **Style refs containing units override prompt facing AND camera** — use
  terrain-only concept crops when the prompt must control pose/heading.
- **NBP's native unit facing is east** (persists even with terrain-only
  refs); don't fight it — generate east, mirror deterministically in the
  foundry (sprites carry no text/insignia, so mirroring is safe).
- **High camera angle only partially takes** (tanks best, infantry worst);
  strict side-profile heading + slightly raised camera is reliably
  achievable and was the accepted register.
- **+1px baked near-black rim** (validated at 50px board scale) is the
  woods/city legibility fix and a deterministic pipeline knob — don't rely
  on NBP outlines alone. **Ground shadows dropped** (read poorly off-plains).
- Per-row variety must be demanded explicitly or NBP returns five clones.

## Phase 9 — Frontend: planning UI

**Goal:** §16.1.

- Select unit → show movement range → assign exact path; assign Barrage/Rest/Hold;
  recruit at controlled cities; review-all + lock → `POST /games/{id}/orders`.
- Advisory predictions: predicted pulses, own-unit traffic-conflict warnings,
  plain-language enemy-ZOC warnings ("may attack, hold, or withdraw; cannot move
  along the enemy line") and illegal ZOC-to-ZOC flags (§16.1).
- Overlays (range halo, path, ZOC) as Pixi graphics layers above the tiles.

**Demonstrable:** plan a real turn end-to-end in the browser and lock it in.

## Phase 10 — Frontend: replay

**Goal:** §16.2–16.3, UI-SPEC §13. Auto-playing, beat-structured replay of the
resolved event log, with fog filtering and a scrollable plain-language event log.
See UI-SPEC §13 for the full design; slices below map the work.

### Slice 1 — API + data plumbing

- ✅ Already done in Phase 5: `GET /games/{id}/turns/{turn}` returns the
  fog-filtered event log and post-turn state. No new backend work needed.
- Frontend: a `useReplay(gameId, turn)` hook that fetches the turn data and
  exposes the ordered `Event[]` list and the pre-turn + post-turn `GameState`
  snapshots.
- Auto-navigate from `GameShell` to `/game/{id}/replay/{turn}` when the poll
  detects a newly resolved turn (turn number advanced + turns row present).
- "Watch last replay" item in the Game menu.

### Slice 2 — Beat engine + playback controls

- A `useBeats(events)` hook that groups the raw `Event[]` into ordered beats
  (UI-SPEC §13.1): Barrage, Pulse 1…N, Capture, Repair, Economy, Recruitment.
  Empty beats are skipped. Exposes `beats`, `currentBeat`, `advance`, `rewind`,
  `jumpTo`.
- Playback controller: auto-play at 1× / 2× with configurable beat durations.
  Manages the play/pause/speed state. The beat engine is pure (no Pixi) and unit-testable.
- Bottom panel control strip: `|◄ ◄ ▶/⏸ ►|`, speed toggle, beat label, Skip button
  (UI-SPEC §13.2). Replaces the order palette / lock control during replay.
- Header phase label shows `REPLAY`; muster/orders fields are hidden.
- No map animation yet — the board snaps to the post-turn state for now (Slice 3
  adds the animations).

**Demonstrable:** navigate to the replay route, step through beats with controls,
see the board snap to each beat's end state.

### Slice 3 — Map animation per beat ✅ done

- ✅ `applyBeats(preTurnState, beats, upToIndex)` pure function
  (`frontend/src/replay/applyBeats.ts`): applies every event from beats 0..N to
  the pre-turn snapshot to produce the board state at any beat boundary — handles
  all eight event kinds (`unit_moved`, `barrage_resolved`, `combat_resolved`,
  `settlement_captured`, `unit_repaired`, `economy_resolved`, `unit_recruited`,
  plus no-ops for `move_blocked`/`traffic_jam`).
- ✅ Per-beat board state: `ReplayShell` computes `boardState = applyBeats(…,
  displayBeat)` and passes it to `Board`; step-back re-renders from the stored
  snapshot (no reverse animation); rewind/jump snap immediately.
- ✅ Animation on step-forward: a rAF loop runs for `ANIM_DURATION_MS` (500ms at
  1×, 250ms at 2×) with ease-in-out-cubic. During animation the board shows the
  pre-beat state; overlays are applied on top via `Board.replayAnim`:
  - **Pulse beats:** unit slides (smooth translation from `origin` to `dest`);
    destroyed-unit fade-out (alpha 1→0; barrage-destroyed and combat-destroyed
    alike).
  - **Recruitment beat:** new unit materializes (fade-in, alpha 0→1).
  - **Capture beat:** settlement color flips instantly when `displayBeat` advances
    (the state change produces the flip; no additional highlight yet).
  - **Barrage, repair, economy beats:** snap (animated effects deferred to a later
    pass — artillery arc, HP tick, muster tick).
- ✅ `Board` gained `ReplayAnimOverrides` prop (`slideOverrides`, `fadingOut`,
  `fadingIn`); `UnitMarker` accepts `posOverride` and `alpha`.
- ✅ Camera on load fits own units in the pre-turn state (already implemented in
  Board; behaviour unchanged by this slice — the auto-fit triggers once on mount
  when `view === null`).
- ✅ Roster panel stays docked, read-only, showing post-turn unit state throughout
  (unchanged from Slice 2 — `replayRosterRows` uses the `state.units` passed to
  Board, which is `boardState` now, not always `postTurnState`; the UI-SPEC calls
  for a static post-turn snapshot here — see note below).

**Demonstrable:** a resolved turn animates smoothly beat by beat (units slide on
pulse beats, destroyed units fade, recruits materialise); step-back snaps to an
earlier board state.

> **Roster note:** The UI-SPEC says the roster should show the *post-turn* unit
> state (static snapshot) throughout the replay. Currently it reflects the
> `boardState` for each beat, which changes. This is a deliberate v0.1 simplification
> (seeing the moving state is arguably useful); a dedicated `postTurnState` roster
> prop can be threaded through in a later pass if needed.

### Slice 4 — Event log panel ✅ done (camera-pan-on-click deferred)

- ✅ Scrollable event log in the right portion of the bottom panel (UI-SPEC §13.3).
  The bottom dock now splits into the control strip (left, `REPLAY_CONTROLS_WIDTH`)
  and the log (`replayLayout` → `replayLog` rect). Wheel scrolls the log;
  off-screen rows are culled so nothing overflows the panel.
- ✅ Beat headers (`▶`/`▸`/`░` glyph by state); `UnitMoved` events collapsed under
  "N units moved"; all other events expanded with §16.3 plain-language text
  (`replay/eventText.ts`, pure + unit-tested). Log model in `replay/replayLog.ts`.
- ✅ Past beats dimmed; current beat highlighted; future beats greyed. The log
  auto-scrolls to keep the current beat in view during playback.
- ✅ Clicking a log entry pauses auto-play and jumps to that beat.
  **Deferred:** panning the camera to the relevant hex on click (a follow-up;
  jump-to-beat lands the beat state, just without the camera move).
- ✅ Fog-filtered: the log renders whatever events the API returns, which are
  already fog-filtered server-side (no extra client work needed).

### Slice 5 — Replay exit + Game menu integration

- After the final beat, "Start Planning" button appears (bottom-right); if the game
  ended, "See Results" leads to the game-over state (UI-SPEC §13.6).
- "Watch last replay" in the Game menu navigates to the replay route for the most
  recently resolved turn.
- End-to-end smoke test: two dev profiles play a turn from lock → poll detects
  resolution → replay auto-launches → steps through → "Start Planning" → planning
  screen for the next turn.

**Milestone — first full playable match in two browsers.**

## Phase 11 — Tuning, scenario polish, success criteria

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
- **P10 — browser-playable** (first full match in two browsers)

## Resolved: auth depth

Phase 5 shipped lightweight opaque tokens (enough to own orders). Phase 7 trades
the originally-planned localStorage-token + secret-link scheme for **lightweight
SSO accounts via Supabase Auth (Google)** — the frontend audience is "you +
recruited friends," and accounts give a clean "log in, land back in your game"
loop with one active game per user. This adds backend work the v0.1 framing had
deferred (JWT validation in FastAPI, `players.user_id`), so it is the part of
Phase 7 most likely to balloon; everything else in the phase is small. Full
rationale and the page/identity model live in [`FRONTEND.md`](./FRONTEND.md).
