# CLAUDE.md

Guidance for working in this repo. See `README.md` for the human overview and `docs/SPEC.md` for the game design.

## What this is

A turn-based hex wargame (planning phase → simultaneous execution). Three deployed tiers:

```
Browser ──> Vercel (Next.js, frontend/) ──> Cloud Run (FastAPI, api/) ──> Supabase (Postgres)
```

The `api/` service is the real workload: it will host the deterministic execution engine (movement pulses, simultaneous combat, capture/economy). The frontend is the planning + replay UI. Supabase holds game state.

## Layout

- `frontend/` — Next.js 16 app (has its own `AGENTS.md`/`CLAUDE.md`).
- `api/` — FastAPI service, **managed with uv** (not pip).
- `infra/setup-gcp.sh` — idempotent GCP provisioning (re-run safely).
- `.github/workflows/` — `frontend.yml`, `api.yml`, `supabase.yml`.
- `docs/SPEC.md` — v0.1 design (the source of truth for game rules).

## Commands

Frontend (`cd frontend`): `npm run dev`, `npm run build`, `npm run lint`.

API (`cd api`): `uv sync`, then `uv run uvicorn app.main:app --reload --port 8080`.
Before committing api changes, run all four — CI fails otherwise:
`uv run pytest` · `uv run black --check .` · `uv run ruff check .` · `uv run mypy .`

## Conventions & gotchas (things that have bitten us)

- **Backend uses uv, never pip.** Deps in `api/pyproject.toml` (`[project.dependencies]` + `[dependency-groups] dev`); Python pinned in `api/.python-version`; lockfile `uv.lock` is committed. The Dockerfile installs via `uv sync --frozen --no-dev`.
- **Next.js 16 is newer than most training data.** `frontend/AGENTS.md` says to read the bundled docs in `frontend/node_modules/next/dist/docs/` before writing app code. Honor that.
- **GCP access is keyless.** Local `gcloud` impersonates `llm-agent-owner@hex-general.iam.gserviceaccount.com` (config `auth/impersonate_service_account`). CI uses Workload Identity Federation. **Never create, download, or commit a service-account JSON key.**
- **Fresh-project API quirk:** impersonated calls bill quota to `hex-general`, so a GCP API must be enabled there (`gcloud services enable …`) before it works, even for reads. Billing must be linked for Cloud Run / Build / Artifact Registry / Secret Manager.
- **Secrets are split by purpose:**
  - Deploy-time → GitHub (`production` environment): `VERCEL_TOKEN`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`. Non-secret CI config → GitHub variables: `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`.
  - Runtime → GCP Secret Manager: `supabase-service-role-key`, mounted into Cloud Run.
- **Supabase key usage:** frontend uses the **publishable** key (`NEXT_PUBLIC_SUPABASE_ANON_KEY`) and is constrained by RLS. The API uses the **service-role** key server-side (bypasses RLS) — it must never reach the browser.
- **New tables need a `service_role` GRANT.** `service_role` bypasses RLS but still needs table-level privileges, which a migration-created table does **not** get by default — the API will 500 with `permission denied for table …`. Add `grant select, insert, update, delete on table public.<t> to service_role;` in the migration.
- **`NEXT_PUBLIC_` defaults: use `||`, not `??`.** The bundler inlines an unset `NEXT_PUBLIC_` var as `""`, and `??` only falls back on null/undefined — so `??` leaves an empty string and the value silently breaks. Use `||` for env-var defaults in frontend code.
- **Deploys are path-filtered.** Touching `api/**` deploys the API; `frontend/**` deploys the frontend. `supabase.yml` only fires on `supabase/**`.
- **Smoke-test a deploy** with the `verify-stack` skill (`.claude/skills/verify-stack/`) — it checks the full browser → Cloud Run → Supabase chain and catches the two gotchas above.

## Working agreements

- Confirm before outward-facing/irreversible actions (pushing, deploying, IAM/billing changes).
- After committing deployable changes, the **`release-monitor`** subagent (`.claude/agents/`) watches the CI/CD pipelines and runs `verify-stack`, returning a compact verdict (keeps the noisy polling/log output out of the main thread). It only pushes when its launch prompt says to, and it diagnoses failures rather than fixing them.
- Prefer `gh` for GitHub (variables/secrets/runs); it's installed and authenticated.
- Live API: `https://hex-api-qfzj5tg56a-uc.a.run.app` (`/health`, `/version`).
