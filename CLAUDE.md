# CLAUDE.md

Guidance for working in this repo. See `README.md` for the human overview and `docs/SPEC.md` for the game design.

## What this is

A turn-based hex wargame (planning phase → simultaneous execution). Three deployed tiers:

```
Browser ──> Vercel (Vite SPA, frontend/) ──> Cloud Run (FastAPI, api/) ──> Supabase (Postgres)
```

The `api/` service is the real workload: it will host the deterministic execution engine (movement pulses, simultaneous combat, capture/economy). The frontend is the planning + replay UI. Supabase holds game state.

**The server runs only world games** (`docs/WORLD_SPEC.md`). The old 2-player match (lobby, invite link, vs-bot, resign, match replay) was retired on 2026-11-10; it is deleted from `api/` and `frontend/`, and `docs/SPEC.md` rules still govern the engine. The world frontend is `frontend/src/world/` (`WorldShell.tsx`, `WorldRenderer.ts`); the hex tile and join modules shared with it stay in `frontend/src/board/`. The retirement is not deployed yet (see `docs/VISION.md` §13).

## Layout

- `frontend/` — Vite + React + TS SPA (has its own `AGENTS.md`/`CLAUDE.md`).
- `api/` — FastAPI service, **managed with uv** (not pip).
- `infra/setup-gcp.sh` — idempotent GCP provisioning (re-run safely).
- `.github/workflows/` — `frontend.yml`, `api.yml`, `supabase.yml`.
- `docs/SPEC.md` — v0.1 design (the source of truth for game rules; its 2-player match flow is retired); `docs/WORLD_SPEC.md` — the world game.

## Commands

Frontend (`cd frontend`): `npm run dev` (Vite, port 5173), `npm run build` (`tsc -b && vite build`), `npm run lint`. After changing API request/response models, regenerate the typed client with `npm run gen:api`.

API (`cd api`): `uv sync`, then `uv run uvicorn app.main:app --reload --port 8080`.
Before committing api changes, run all four — CI fails otherwise:
`uv run pytest` · `uv run black --check .` · `uv run ruff check .` · `uv run mypy .`

Local dev loop: `./scripts/dev.sh up` owns the whole per-worktree lifecycle —
it renders + starts an isolated Supabase stack, seeds accounts, writes env, and
runs both app servers **detached, logging to files under `.dev/`**. Each worktree
gets its own instance (auto-allocated ports, stride 100) so several can run at
once; `HEX_SUPABASE_FULL=1` un-leans the stack. Don't launch the dev servers as
foreground/`run_in_background` Bash calls (their streaming output floods this
context). To health-check them, **spawn the `dev-doctor` subagent**
(`.claude/agents/dev-doctor.md`, Haiku); it reads the logs in its own context and
returns a compact verdict, restarts a cleanly-dead server, and escalates real
faults rather than fixing them. See `docs/LOCAL_DEV.md`. To get a world to look at: `./scripts/game-setup.sh create-world`, then `join <game_id>` (and `tick <game_id>` to resolve a turn); the app opens it at `/world/<game_id>`.

**Art generation goes to Codex from now on** (user, 2026-11-08): new art (tiles,
tokens, UI and strategic-map art) is generated with Codex (the `codex` CLI and its
built-in image generation), not the NBP/`tile-smith` loop below. The NBP foundry stays
as the record and reproducer for tiles already shipped. The ratification rule below is
unchanged. See `docs/VISION.md` §11.

Board tileset work (painterly hex tiles in `frontend/src/board/tiles/`, 128px
flat-top RGBA, plus same-terrain join sprites in `frontend/src/board/joins/`): the
shipped terrain tiles come from the Codex paint-in toolchain,
`scripts/tile-foundry/paintin.py` (sheet, Codex, cut, rim pin, gates) and
`scripts/tile-foundry/joins.py`, ratified 2026-11-09; work dirs, raws and
provenance are in `art/codex/terrain/` (`APPLIED.md` traces each shipped tile).
Preview candidates at board scale offline with `python3 scripts/board-mock.py`
(`--tiles`, `--joins`, `--map scripts/board-mock-maps/...`), which uses the
frontend's exact selection rules. **`docs/ART_PIPELINE.md` is the canonical
contract** ("Paint-in recipe", asset spec, command quick-ref). The NBP foundry
(`art/foundry/`, `tile-smith` subagent) is superseded for terrain but still builds
the unit sprites, and `scripts/tile-foundry/selftest.py` keeps verifying it.
`python3 scripts/tile-montage.py <dir|globs> --out <path>` renders a labelled
variant montage; use the **`verify-board`** skill to render the local board in
Chrome after an art/render change. See ART_PIPELINE.md + ROADMAP Phase 8.

Lakes (`Terrain.LAKE`, map char `o`, generated worlds only) have **ratified art
(round 2, 2026-11-10)**: a lake hex draws the tile painted for its **neighbour mask**
(bit d set when the neighbour in `AXIAL_DIRECTIONS[d]` is lake, off-map is land; 64
masks, `frontend/src/board/lakeTiles.ts`, a port of board-mock's `lake_mask` /
`lake_tile_key`), from `frontend/src/board/tiles/lake/m<MM>_<VV>.png`. River **mouths**
(`frontend/src/board/joins/lake/mouth_*`, drawn where `riverMouths` says a river runs
into a lake) are the only lake join: no lake edge or vertex joins. The files are byte
copies of `art/codex/terrain/candidates/lake2/` and `lake2-joins/`
(`scripts/tests/test_lake_tiles_sync.py`); record and rebuild in
`art/codex/terrain/lake/README.md` ("Round 2"; round 1 is superseded), per-tile
provenance in `art/codex/terrain/APPLIED.md`; `scripts/lake-fixture.py` pins the frontend
port to board-mock. Road and river junctions are drawn from a pruned
course graph (`frontend/src/board/courseGraph.ts`, ported in `scripts/board-mock.py`).

The ratified site and base tiles (farm, oil field, steel mill, factory levels 1-3, base
stages 1-4; 2026-11-10) are wired into the world renderer via
`frontend/src/world/siteTiles.ts` (tile choice in `worldTiles.ts`): a site's painted
tile replaces its hex's terrain tile, a founded base draws its tile, cities and vacant
base slots keep terrain. The files in `frontend/src/world/siteTiles/` are byte copies of
`art/codex/terrain/candidates/sites-ratified/` (README there, provenance in
`art/codex/terrain/SITES.md`; `scripts/tests/test_site_tiles_sync.py` checks the copies).
Bases are drawn at stage 1 only until base growth is designed (`baseStageOf`).
Changing these tiles is hex art under the sign-off rule below.

- **Hex art is the user's call — never auto-ratify.** Before you apply, overwrite,
  or commit *any* change to tiles under `frontend/src/board/tiles/` (new variants,
  regens, recolours, or programmatic edits like the river bank-rough pass),
  **render a before/after montage, publish it as a claude.ai Artifact, and give the
  user the artifact link, then wait for their direction.** Build the page with
  `scripts/review-page.py` (self-contained HTML, data-URI images; subagents render
  the montages and the page, the main agent publishes with the Artifact tool) —
  the user reviews from anywhere, so localhost montage links are **not** the
  deliverable (user, 2026-07-04). `./scripts/dev.sh montage up` still serves
  `.dev/montages/` on port `8011+100N` for quick local self-checks only. This
  applies whether the tiles came from PixelLab/NBP or a deterministic script; the
  main agent prepares the candidate, the **user** picks. (Board-scale
  `verify-board` is for *after* a pick is ratified, not a substitute for the
  pre-apply sign-off.)

## API code standards: trust internal invariants, validate only at boundaries

The `api/` engine is a pure, deterministic core. Write it to **trust its own invariants** and crash loudly when they're violated. Do **not** pepper internal code with null checks, `isinstance` guards, or `hasattr` to defend against states that "can't happen." If such a state does happen, that's a bug to fix at its source — a bare `KeyError`/`AttributeError` with a stack trace is a *better* outcome than silently coping and resolving a turn into a wrong game state. Prefer this even to a fault: when unsure whether a guard is needed, leave it out and let it crash.

- **Validate at boundaries, assume within.** *Boundaries* are where untrusted/external data enters: the scenario loader (`scenario.py`), settings (`config.py`), DB responses (`stats.py`), and HTTP request bodies. There, validate explicitly and raise a typed error with context (`ScenarioError`, `RuntimeError`, pydantic `ValidationError`); `isinstance`/`is None` checks belong here. Once data has crossed a boundary it is trusted — internal engine code indexes dicts directly (`units[uid]`), accesses attributes directly, and lets an invariant violation raise.
- **Fail loud, never silent.** Logging an error then returning a "safe" default, swallowing an exception, or substituting `None`/`0`/`[]` for a value you couldn't compute are all banned. If you can't produce a correct result, raise.
- **`None` means one well-defined thing, or don't use it.** `X | None` is only for genuine domain optionality with a single documented meaning (`Settlement.controlled_by is None` ⇒ neutral; `unit_at()` ⇒ "no unit here"). Never use `None` for "not computed yet," as an error signal, or for two different kinds of absence.
- **These are *not* "excessive caution" — they're fine:** boundary validation that raises; `*_at()` probes returning `X | None` because absence is a real answer; union dispatch like `isinstance(order, MoveOrder)` to pick an algorithm branch; and engine contract guards that **raise** with context (`EngineError` for an unknown unit or non-contiguous path) — those convert a bad caller into a clear failure, which is fail-loud, not silent-coping.
- **Smell test:** if a check, when it fails, would silently skip work or return a plausible-but-wrong value, delete the check and let it crash — then fix whatever produced the bad state.

## Conventions & gotchas (things that have bitten us)

- **Backend uses uv, never pip.** Deps in `api/pyproject.toml` (`[project.dependencies]` + `[dependency-groups] dev`); Python pinned in `api/.python-version`; lockfile `uv.lock` is committed. The Dockerfile installs via `uv sync --frozen --no-dev`.
- **Frontend is a Vite SPA, not Next.js.** Vite + React 19 + React Router v8 + Tailwind v4; no server/SSR, no `next/*`. See `frontend/AGENTS.md`. The API types in `frontend/src/lib/api/schema.d.ts` are generated (`npm run gen:api`) — don't hand-edit; import model types from `@/lib/api/types`.
- **GCP access is keyless.** Local `gcloud` impersonates `llm-agent-owner@hex-general.iam.gserviceaccount.com` (config `auth/impersonate_service_account`). CI uses Workload Identity Federation. **Never create, download, or commit a service-account JSON key.**
- **Fresh-project API quirk:** impersonated calls bill quota to `hex-general`, so a GCP API must be enabled there (`gcloud services enable …`) before it works, even for reads. Billing must be linked for Cloud Run / Build / Artifact Registry / Secret Manager.
- **Secrets are split by purpose:**
  - Deploy-time → GitHub (`production` environment): `VERCEL_TOKEN`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`. Non-secret CI config → GitHub variables: `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`.
  - Runtime → GCP Secret Manager: `supabase-service-role-key`, mounted into Cloud Run.
  - Local dev tooling → gitignored `.claude/settings.local.json` `env` block: `PIXELLAB_API_KEY`. The tracked `.mcp.json` references it as `${PIXELLAB_API_KEY}` — **never hardcode the token in `.mcp.json`** (it's tracked, so a raw token risks an accidental commit).
- **Supabase key usage:** frontend uses the **publishable** key (`VITE_SUPABASE_ANON_KEY`) and is constrained by RLS. The API uses the **service-role** key server-side (bypasses RLS) — it must never reach the browser.
- **New tables need a `service_role` GRANT.** `service_role` bypasses RLS but still needs table-level privileges, which a migration-created table does **not** get by default — the API will 500 with `permission denied for table …`. Add `grant select, insert, update, delete on table public.<t> to service_role;` in the migration.
- **`VITE_` defaults: use `||`, not `??`.** Vite inlines an unset `VITE_` var as `""`, and `??` only falls back on null/undefined — so `??` leaves an empty string and the value silently breaks. Use `||` for env-var defaults in frontend code. (Browser config is `VITE_`-prefixed and read via `import.meta.env`.)
- **Deploys are path-filtered.** Touching `api/**` deploys the API; `frontend/**` deploys the frontend. `supabase.yml` only fires on `supabase/**`.
- **Smoke-test a deploy** with the `verify-stack` skill (`.claude/skills/verify-stack/`) — it checks the full browser → Cloud Run → Supabase chain and catches the two gotchas above.

## Working agreements

- **Branching: commit straight to `main`, no feature branches, no PRs.** We're pre-launch and moving fast — commit and push to `main` directly. (We'll switch to feature branches + PRs once we're live.) This overrides any default "branch first on the default branch" behavior.
- Confirm before outward-facing/irreversible actions (pushing, deploying, IAM/billing changes).
- **After committing deployable changes, hand off to the `release-monitor` subagent** (`.claude/agents/`) to watch the CI/CD pipelines and run `verify-stack` — do **not** babysit the deploy inline with `gh run watch`/`gh run list`. This convention overrides any general "don't spawn subagents unless asked" default: the agent exists to keep noisy polling/log output out of the main thread and hand back a compact verdict. It only pushes when its launch prompt says to, and it diagnoses failures rather than fixing them.
- Prefer `gh` for GitHub (variables/secrets/runs); it's installed and authenticated.
- Live API: `https://hex-api-qfzj5tg56a-uc.a.run.app` (`/health`, `/version`).
