# hex-general

A prototype **turn-based operational hex wargame** with a twist: players issue orders during a planning phase, then all orders execute **simultaneously**. Inspired by *Panzer General* / *Civil War General*. See [`docs/SPEC.md`](docs/SPEC.md) for the full v0.1 design.

> Status: early scaffold. The platform (frontend, API, database, and CI/CD) is deployed and proven end-to-end; the game itself is not built yet.

## Architecture

```
Browser ──> Vercel (Next.js frontend) ──> Cloud Run (FastAPI API) ──> Supabase (Postgres)
```

| Tier | Tech | Hosting | Notes |
| ---- | ---- | ------- | ----- |
| Frontend | Next.js 16 (App Router, TypeScript, Tailwind) | Vercel | Talks to Supabase (publishable key, RLS-protected) and to the API |
| API | FastAPI (Python 3.13, [uv](https://docs.astral.sh/uv/)) | Cloud Run (`hex-api`, `us-central1`) | Hosts the deterministic execution engine; reaches Supabase server-side with the service-role key |
| Database / Auth | Supabase (Postgres) | Supabase Cloud | Project ref `amhkpsrvjlthmmemwssc` |

Everything deploys via **GitHub Actions** on push to `main` (path-filtered, see below). GCP auth from CI is **keyless** (Workload Identity Federation) — no service-account JSON keys exist anywhere.

## Repository layout

```
docs/SPEC.md            Game design spec (v0.1)
frontend/               Next.js app (deployed to Vercel)
api/                    FastAPI service (deployed to Cloud Run; uv-managed)
infra/setup-gcp.sh      One-time GCP provisioning (WIF, deployer/runtime SAs, Artifact Registry, Secret Manager)
.github/workflows/      CI/CD: frontend.yml, api.yml, supabase.yml
.mcp.json               MCP server config (Supabase + PixelLab). Secrets are NOT
                        stored here — the PixelLab token is injected via the
                        ${PIXELLAB_API_KEY} env var (see Local development)
```

## Local development

To run the full stack on a laptop and play a match offline (local Supabase +
seeded dev accounts), see **[`docs/LOCAL_DEV.md`](docs/LOCAL_DEV.md)** — `./scripts/dev-up.sh`
brings up Postgres + Auth, seeds dev accounts, and wires the env files.

Running a tier on its own against existing config:

### Frontend (Vite SPA)
```bash
cd frontend
npm install
cp .env.example .env.local   # fill in (values are browser-safe public keys)
npm run dev                  # http://localhost:5173
```

### API
```bash
cd api
uv sync
uv run uvicorn app.main:app --reload --port 8080   # http://localhost:8080/health
```
Checks: `uv run pytest`, `uv run black --check .`, `uv run ruff check .`, `uv run mypy .`

### MCP servers (Supabase + PixelLab)

`.mcp.json` is tracked and **must stay secret-free**. The PixelLab server reads its
token from the `${PIXELLAB_API_KEY}` env var instead of hardcoding it. Set the key in
your gitignored `.claude/settings.local.json` (Claude Code expands it into `.mcp.json`):

```jsonc
// .claude/settings.local.json  (gitignored)
{
  "env": { "PIXELLAB_API_KEY": "<your-pixellab-token>" }
}
```

Or export `PIXELLAB_API_KEY` in your shell profile. Never paste the raw token into
`.mcp.json` — it's tracked and would risk committing the secret.

## Deployment (CI/CD)

Pushes to `main` trigger the relevant workflow based on which paths changed:

| Workflow | Triggers on | Deploys |
| -------- | ----------- | ------- |
| `frontend.yml` | `frontend/**` | Vercel production (`vercel deploy --prod`) |
| `api.yml` | `api/**` | uv tests → Docker build → Cloud Run (keyless WIF) |
| `supabase.yml` | `supabase/**` | `supabase db push` (no migrations yet) |

Deploy jobs run under the `production` GitHub Environment. Configuration lives in:
- **GitHub variables**: `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`
- **GitHub secrets**: `VERCEL_TOKEN`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`
- **GCP Secret Manager**: `supabase-service-role-key` (mounted into Cloud Run at runtime)

## Next steps

1. Supabase schema for the v0.1 data model (games, hexes, units, orders, turns) + RLS — this also activates `supabase.yml`.
2. The API execution engine (planning, order lock, simultaneous pulse resolution, combat, capture/economy).
3. The frontend planning UI + execution replay.
