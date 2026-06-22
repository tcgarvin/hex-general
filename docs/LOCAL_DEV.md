# Local development & laptop playtesting

Run the whole stack — Postgres + Auth + API + frontend — on a laptop, and play a
full two-sided match offline, without touching the deployed project or
configuring Google OAuth. This is the Phase 7 "Slice 3" loop (see
[`ROADMAP.md`](./ROADMAP.md) and [`FRONTEND.md`](./FRONTEND.md)).

You sign in as a seeded **`@hex.local`** dev account against the *local* Supabase
Auth, then use the **dev-profile switcher** to impersonate the other identity and
drive both sides of a game.

## How auth works locally (why it "just works")

The API verifies Supabase JWTs as **ES256 via the JWKS endpoint**
(`api/app/games/auth.py`) — identically in prod and locally. A default
`supabase start` would sign tokens with HS256, which the API rejects, so the
local stack is configured (`supabase/config.toml` → `[auth].signing_keys_path`)
to use an **asymmetric ES256 signing key** instead. `scripts/dev-up.sh` generates
that throwaway key (`supabase gen signing-key`) before starting the stack. The
legacy `service_role` key the API uses for DB writes keeps working alongside it.

Net effect: **no local-only auth code path** — the identity boundary is the same
everywhere.

## Prerequisites

- **Docker** running, and your user able to reach the daemon (in the `docker`
  group, or start it with `sudo systemctl start docker`). `supabase start` runs
  the stack in Docker.
- **Supabase CLI** ≥ 2.107 (needs local ES256 signing-key support).
  [Install](https://supabase.com/docs/guides/local-development).
- **uv** (backend) and **Node/npm** (frontend).

## Bring it up

```bash
./scripts/dev-up.sh
```

This is idempotent. It generates the local signing key, `supabase start`s the
stack, seeds the dev accounts, and writes the env files:

- `api/.env` — `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `DEV_USER_IDS`
  (the two seeded accounts, so the API treats them as devs).
- `frontend/.env.local` — points the Vite app at the local stack (your previous
  `.env.local`, if any, is backed up to `.env.local.bak`).

Then start the two servers in separate terminals:

```bash
cd api && uv run uvicorn app.main:app --reload --port 8080
cd frontend && npm run dev          # http://localhost:5173
```

## Play

1. Open http://localhost:5173 and **sign in with email + password**:
   - `dev1@hex.local` / password in `api/app/dev_seed.py` (`DEV_PASSWORD`).
2. Confirm you're a dev: `GET /me` reports `is_dev: true` (the switcher appears).
3. Create a game as `dev1`, then use the **dev-profile switcher** to act as
   `dev2@hex.local`, claim the open slot, and submit orders for both sides. The
   `GameView` advances as each side locks in.

Seeded accounts (same password for both):

| Email            | Role in a match           |
| ---------------- | ------------------------- |
| `dev1@hex.local` | side A (create the game)  |
| `dev2@hex.local` | side B (join, via switch) |

To add or rename accounts, edit `DEV_USER_EMAILS` in `api/app/dev_seed.py` and
re-run `./scripts/dev-up.sh` (re-seeding is idempotent).

## Tear down

```bash
./scripts/dev-down.sh
```

Stops the stack and restores your original `frontend/.env.local` from the backup.
`api/.env` is left in place (gitignored).

## Notes

- **Migrations** in `supabase/migrations/` apply automatically on `supabase start`
  / `supabase db reset`.
- **Supabase Studio** (local DB browser) runs at http://127.0.0.1:54323.
- Everything under `[api]`/`[db]`/`[auth]` in `supabase/config.toml` is
  local-only; the deploy workflow runs `supabase db push` (migrations) and never
  pushes this config to the deployed project.
