# Local development & laptop playtesting

Run the whole stack — Postgres + Auth + API + frontend — on a laptop, and play a
full two-sided match offline, without touching the deployed project or
configuring Google OAuth.

One command owns the whole lifecycle:

```bash
./scripts/dev.sh up        # isolated Supabase stack + API + frontend, then wait for health
```

`dev.sh` is **per-worktree**: every git worktree gets its own isolated stack on
its own ports, so you can run several branches side by side (see
[Multiple worktrees](#multiple-worktrees-at-once) below). You sign in as a seeded
**`@hex.local`** dev account against the *local* Supabase Auth, then use the
**dev-profile switcher** to impersonate the other identity and drive both sides.

## How auth works locally (why it "just works")

The API verifies Supabase JWTs as **ES256 via the JWKS endpoint**
(`api/app/games/auth.py`) — identically in prod and locally. A default
`supabase start` would sign tokens with HS256, which the API rejects, so the
local stack is configured (`supabase/config.toml` → `[auth].signing_keys_path`)
to use an **asymmetric ES256 signing key** instead. `dev.sh` generates that
throwaway key (`supabase gen signing-key`) into the rendered workdir before
starting the stack. The legacy `service_role` key the API uses for DB writes
keeps working alongside it.

Net effect: **no local-only auth code path** — the identity boundary is the same
everywhere.

## Prerequisites

- **Docker** running, and your user able to reach the daemon (in the `docker`
  group, or start it with `sudo systemctl start docker`).
- **Supabase CLI** ≥ 2.107 (needs local ES256 signing-key support).
  [Install](https://supabase.com/docs/guides/local-development).
- **uv** (backend) and **Node/npm** (frontend).

## Bring it up

```bash
./scripts/dev.sh up
```

Idempotent. In one pass it: renders this worktree's Supabase config, starts the
stack (Postgres + Auth, lean by default — see below), seeds the dev accounts,
`npm install`s the frontend on first run in a fresh worktree, writes the env
files, and starts both app servers detached (logging to files under `.dev/`,
gitignored — the main Claude agent never babysits streaming logs).

It writes:

- `api/.env` — `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DEV_USER_IDS`.
- `frontend/.env.local` — `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`,
  `VITE_API_URL` (this instance's API port), and `VITE_DEV_PORT` (Vite's port).
  A prior `.env.local`, if any, is backed up to `.env.local.bak`.

Manage the running stack:

```bash
./scripts/dev.sh status            # health of api / frontend / montage / supabase
./scripts/dev.sh logs api 80       # tail a server's log (api|frontend|montage)
./scripts/dev.sh down              # stop the app servers + Supabase stack
./scripts/dev.sh restart           # down + up
```

When working with Claude in this repo, ask it to **spawn the `dev-doctor`
subagent** (`.claude/agents/dev-doctor.md`, runs on Haiku) to health-check the
servers — it reads the logs in its own context and reports a one-screen verdict,
so the main thread doesn't fill up with server output.

## Multiple worktrees at once

Each worktree's stack is keyed by an integer **`HEX_INSTANCE`** (starting at 0),
and **every port is derived from it with a stride of 100**:

| Service      | Port           | Instance 0 | Instance 1 |
| ------------ | -------------- | ---------- | ---------- |
| Frontend     | `5173 + 100N`  | 5173       | 5273       |
| API          | `8080 + 100N`  | 8080       | 8180       |
| Supabase API | `54321 + 100N` | 54321      | 54421      |
| Montage      | `8011 + 100N`  | 8011       | 8111       |

(The rest of the Supabase ports — db, shadow, pooler, studio, inbucket,
analytics, edge inspector — are offset the same way inside the rendered config.)

You normally don't set `HEX_INSTANCE`: `dev.sh up` **auto-allocates the first
free slot** and records it in `.dev/instance` (gitignored), so the worktree keeps
the same ports across `up`/`down`. Each worktree also gets a distinct Supabase
`project_id` (derived from its path), which namespaces its Docker containers and
data volumes — so worktrees never share state. Pin a slot explicitly if you want:

```bash
HEX_INSTANCE=2 ./scripts/dev.sh up
```

### How the isolation works (and why deploys are unaffected)

The committed `supabase/config.toml` is the **base** (instance 0). `dev.sh` runs
`scripts/render-dev-config.py` to produce `.dev/supabase/config.toml` with the
ports + `project_id` offset for this instance (and the auth redirect origin
retargeted to the instance's Vite port), then runs the stack from there via
`supabase --workdir .dev`. This is Supabase's
[documented pattern](https://github.com/orgs/supabase/discussions/5968) for
multiple local stacks (distinct `project_id` + ports, driven by `--workdir`).

Because the offset config is generated under `.dev/` and never committed, CI and
a bare `supabase start` use the committed `supabase/config.toml` **verbatim** —
the migrate workflow (`supabase.yml`) is completely unaffected. (`env()`
interpolation isn't used for the ports: it can't be, because that workflow parses
the whole config and the unset vars would fail the parse — the rendered-workdir
approach sidesteps that entirely.)

### Lean by default

A full Supabase stack is ~11 containers / ~2.2 GiB. The app only needs **Auth +
Postgres** locally (its "presence" is polled via the API, not Supabase Realtime),
so `dev.sh` renders a **lean** stack (~6 containers / ~1 GiB) with **studio /
realtime / inbucket / analytics disabled**. When you actually need them (e.g. to
browse the DB in Studio), turn them back on for a worktree with a one-shot flag —
no file edit:

```bash
HEX_SUPABASE_FULL=1 ./scripts/dev.sh up
```

## Board-tile montage server

Tile-art review (see `CLAUDE.md`) uses a small static server for hosting
montages. It's managed by `dev.sh` (on its instance-offset port, docroot
`.dev/montages/`) but **on demand**, since it's only for board work:

```bash
./scripts/dev.sh montage up                       # serve .dev/montages/ on 8011+100N
python3 scripts/tile-montage.py <dir|globs>        # --out defaults to .dev/montages/
./scripts/dev.sh montage down
```

`scripts/tile-montage.py` defaults `--out` to `.dev/montages/montage.png`, so a
freshly rendered montage is immediately reachable at
`http://127.0.0.1:<montage-port>/montage.png`.

## Play

1. Open the App URL printed by `dev.sh up` and **sign in with email + password**:
   - `dev1@hex.local` / password in `api/app/dev_seed.py` (`DEV_PASSWORD`).
2. Confirm you're a dev: `GET /me` reports `is_dev: true` (the switcher appears).
3. Create a game as `dev1`, then use the **dev-profile switcher** to act as
   `dev2@hex.local`, claim the open slot, and submit orders for both sides.

Seeded accounts (same password for both):

| Email            | Role in a match           |
| ---------------- | ------------------------- |
| `dev1@hex.local` | side A (create the game)  |
| `dev2@hex.local` | side B (join, via switch) |

To add or rename accounts, edit `DEV_USER_EMAILS` in `api/app/dev_seed.py` and
re-run `./scripts/dev.sh up` (re-seeding is idempotent).

## Tear down

```bash
./scripts/dev.sh down
```

Stops the app servers and this worktree's Supabase stack, and restores your
original `frontend/.env.local` from the backup. `.dev/instance` is kept so the
worktree reuses its slot next time; `api/.env` is left in place (gitignored).

## Notes

- **Migrations** in `supabase/migrations/` apply automatically on `supabase
  start` (the rendered workdir symlinks back to the real migrations dir).
- **Supabase Studio** is off in the lean stack; bring it up with
  `HEX_SUPABASE_FULL=1` (then it's at `http://localhost:$((54323 + 100·N))`).
- Everything below `project_id` in `supabase/config.toml` is local-only; the
  deploy workflow runs `supabase db push` (migrations) and never pushes config.
