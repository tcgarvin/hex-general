# Local development & laptop playtesting

Run the whole stack — Postgres + Auth + API + frontend — on a laptop, and play a
world game offline, without touching the deployed project or
configuring Google OAuth.

One command owns the whole lifecycle:

```bash
./scripts/dev.sh up        # isolated Supabase stack + API + frontend, then wait for health
```

`dev.sh` is **per-worktree**: every git worktree gets its own isolated stack on
its own ports, so you can run several branches side by side (see
[Multiple worktrees](#multiple-worktrees-at-once) below). You sign in as a seeded
**`@hex.local`** dev account against the *local* Supabase Auth, then use the
**dev-profile switcher** to impersonate another account (for example to seat a
second human in the same world).

The server runs only world games; the 2-player match (and its vs-bot mode) was
retired on 2026-11-10, so there is no "new game" button and no `/games` API. You
play by creating a world and joining it (below).

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
3. Get a world to play. The start screen (`/`) lists open worlds with a "Join" button;
   a fresh local database has none, so create one first (dev-only API, below).
   Once you hold a seat, `/` redirects to `/world/<game_id>`.

Create, join and tick a world from the shell with `scripts/game-setup.sh` (it reads
this worktree's URLs from `frontend/.env.local` and talks only to the local stack;
`email` defaults to `dev1@hex.local`):

```bash
./scripts/game-setup.sh create-world --slots 8 --bots 4   # prints the world JSON (game_id); does not seat you
./scripts/game-setup.sh join <game_id>                     # seat dev1 in the first open slot
./scripts/game-setup.sh tick <game_id>                     # resolve a turn now (dev-only)
./scripts/game-setup.sh active                             # {"active_world": {"game_id", "slot"} | null}
./scripts/game-setup.sh clear                              # delete the caller's active world
./scripts/game-setup.sh delete-world <game_id>             # delete any world (dev-only)
```

`create-world` defaults to `--slots 8 --bots 4 --seed 0 --clock manual`
(`--clock twice_daily` schedules the real 01:00 / 13:00 clock; the API caps `--slots` at 400).

Seeded accounts (same password for all):

| Email            | Role                                                        |
| ---------------- | ----------------------------------------------------------- |
| `dev1@hex.local` | default pilot                                               |
| `dev2@hex.local` | a second human (join via the switcher or `game-setup.sh join <id> dev2@hex.local`) |
| `dev3@hex.local` | spare for automated checks that need a fresh world          |

An account can hold only one active world, so checks that need a new world use
`dev3` rather than clearing a playtest on `dev1` or `dev2`.

Local worlds are disposable: do not add dev accounts to get a fresh world. Run
`scripts/game-setup.sh clear <email>` or `scripts/game-setup.sh delete-world <id> [email]`
(both call the dev-only `DELETE /dev/worlds/{id}`, which removes the world's game,
players, turns and map).

To add or rename accounts, edit `DEV_USER_EMAILS` in `api/app/dev_seed.py` and
re-run `./scripts/dev.sh up` (re-seeding is idempotent).

### A world playtest on a fast clock

World games (WORLD_SPEC) resolve on a clock rather than when everyone submits.
Locally, create a world (`game-setup.sh create-world`, or `POST /dev/worlds` with
e.g. `{"slots": 200, "bots": 100}`), join it (`game-setup.sh join <id>`,
`POST /worlds/{id}/join`, or the "Join" button on the start screen), then drive its
clock with the tick loop, detached and logging under `.dev/`:

```bash
cd api && setsid nohup uv run python -m app.world.tick_loop \
  --game <world id> --every 600 > ../.dev/tick-loop.log 2>&1 < /dev/null &
```

It waits one interval before the first tick, and publishes each next tick time
so the world header counts down. Stop it with `pkill -f '^uv run python -m app.world.tick_loop'`
(the anchor keeps the pattern from matching the shell that runs `pkill`); it
clears the countdown on exit. `POST /dev/worlds/{id}/tick` resolves a turn
immediately. A user can hold one active world at a time; `game-setup.sh clear`
deletes the caller's.

### Watch a whole bot season offline

The season viewer plays a headless bot world back on a map, with no dev stack
needed. First record a season. The recorder runs the world the same way the
simulator does and writes a compact JSON record of the map, every unit, site
owners, player numbers and the located events of each turn:

```bash
cd api && uv run python -m app.world.season --slots 200 --bots 100 --seed 1 \
  --turns 60 --out ../.dev/season/season-200x100-seed1.json
```

`--bots` defaults to every slot. A 200-slot world with 100 bots takes about
five minutes for 60 turns; 12 slots take under half a minute. Then build the
page:

```bash
python3 scripts/season-viewer.py .dev/season/season-200x100-seed1.json \
  --out .dev/season/season-200x100-seed1.html
```

The result is one self-contained HTML file that opens straight from disk. It
shows the map with territory, units and the turn's fights and captures, a
timeline with play controls, a sortable players table and charts. Open it at
`#turn-30` to start on turn 30, or `#turn-30-b012` to also select player
`b012`. Add `--fragment` for the form the claude.ai Artifact tool takes, and
`--title` to name the page. The page's CSS, markup and script live in
`scripts/season_viewer/`.

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
