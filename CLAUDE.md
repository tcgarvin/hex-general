# CLAUDE.md

Guidance for working in this repo. See `README.md` for the human overview and `docs/SPEC.md` for the game design.

## What this is

A turn-based hex wargame (planning phase → simultaneous execution). Three deployed tiers:

```
Browser ──> Vercel (Vite SPA, frontend/) ──> Cloud Run (FastAPI, api/) ──> Supabase (Postgres)
```

The `api/` service is the real workload: it will host the deterministic execution engine (movement pulses, simultaneous combat, capture/economy). The frontend is the planning + replay UI. Supabase holds game state.

## Layout

- `frontend/` — Vite + React + TS SPA (has its own `AGENTS.md`/`CLAUDE.md`).
- `api/` — FastAPI service, **managed with uv** (not pip).
- `infra/setup-gcp.sh` — idempotent GCP provisioning (re-run safely).
- `.github/workflows/` — `frontend.yml`, `api.yml`, `supabase.yml`.
- `docs/SPEC.md` — v0.1 design (the source of truth for game rules).

## Commands

Frontend (`cd frontend`): `npm run dev` (Vite, port 5173), `npm run build` (`tsc -b && vite build`), `npm run lint`. After changing API request/response models, regenerate the typed client with `npm run gen:api`.

API (`cd api`): `uv sync`, then `uv run uvicorn app.main:app --reload --port 8080`.
Before committing api changes, run all four — CI fails otherwise:
`uv run pytest` · `uv run black --check .` · `uv run ruff check .` · `uv run mypy .`

Local dev loop: `./scripts/dev-up.sh` (Supabase + env, once), then
`./scripts/dev.sh up` to run both app servers **detached, logging to files under
`.dev/`** — don't launch the dev servers as foreground/`run_in_background` Bash
calls (their streaming output floods this context). To health-check them, **spawn
the `dev-doctor` subagent** (`.claude/agents/dev-doctor.md`, Haiku); it reads the
logs in its own context and returns a compact verdict, restarts a cleanly-dead
server, and escalates real faults rather than fixing them. See `docs/LOCAL_DEV.md`.

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
