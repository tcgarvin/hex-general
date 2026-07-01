# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## `api.yml` deploys on frontend-only pushes (missing/broken path filter)

The API (Cloud Run) workflow fired for a **frontend-only** push — commit `9d3eb02`
(regen river/bridge tiles under `frontend/src/board/tiles/`), API run
[`28533612068`](https://github.com/tcgarvin/hex-general/actions/runs/28533612068).
Per our convention deploys are path-filtered (`api/**` → API, `frontend/**` →
frontend), so `api.yml` should not run here. Check `.github/workflows/api.yml` for
a proper `paths: [api/**]` filter (frontend.yml has one; api.yml likely lacks or
misconfigures it). Harmless but causes needless API redeploys on every frontend
commit.

## Per-worktree local dev (parallel worktrees, isolated stacks)

Make it possible to run multiple git worktrees concurrently, each with a fully
isolated local environment (its own Supabase stack + app servers + montage
server), segregated at the worktree + port level — **no Docker-per-app**, just
distinct ports and distinct Supabase `project_id` per worktree.

### Design (decided)

- **Single entrypoint.** Collapse `dev-up.sh` / `dev-down.sh` into `dev.sh`, which
  owns the whole per-worktree lifecycle: Supabase + env files + the two app
  servers + the montage server. Surface: `dev.sh up|down|status|logs|montage`.
- **`HEX_INSTANCE` (integer) drives everything.** `dev.sh` validates it and, when
  unset, **auto-allocates the first free slot** and records it in gitignored
  `.dev/instance`. All ports derive from it with a **stride of 100**:
  - app API `8080 + 100*N`, Vite `5173 + 100*N`, montage `8011 + 100*N`
  - Supabase api `54321 + 100*N`, db `54322 + 100*N`, studio `54323 + 100*N`,
    **and the rest** (auth/inbucket/analytics/pooler — every host-bound port the
    CLI uses, not just the three in `config.toml` today).
- **Isolated Supabase per worktree.** Each worktree gets a distinct `project_id`
  (namespaces the Docker containers) + the offset ports above. Stacks start clean
  (migrations + seed on `start`); each worktree owns its own teardown. Renaming
  `project_id` per worktree is **safe** — see "CI is unaffected" below.
- **Lean by default.** A full stack is **11 containers / ~2.2 GiB**. Disable the
  services dev doesn't need — analytics (logflare, ~656 MiB), realtime (~292),
  studio (~293), vector (~51), inbucket (~11) — via `enabled = false` in config,
  cutting a stack to ~6 containers / ~1 GiB. Re-enable for a worktree with a
  one-shot env flag, **not** a file edit: `HEX_SUPABASE_FULL=1 ./scripts/dev.sh up`
  (turns Studio etc. back on when you actually need to browse the DB). Document
  this escape hatch in `docs/LOCAL_DEV.md`.
- **Montage server becomes managed.** Today it's run by hand
  (`python3 -m http.server 8011`) → dangling processes. Fold it into `dev.sh`'s
  existing `start_server`/`stop_server` pattern (tracked pid + log in `.dev/`,
  killed on `down`), on its offset port, serving a **fixed docroot `.dev/montages/`**.
  Default `scripts/tile-montage.py --out` to that dir so the server has a stable
  root. Make it on-demand (`dev.sh montage up|down`) rather than auto-started by
  `up`, since it's only for tile work. (See the hex-art sign-off note in
  `CLAUDE.md` — this replaces the manual host step there.)

### Env-file impact

`dev-up`'s job moves into `dev.sh up`. It must write `api/.env` and
`frontend/.env.local` with the **instance-offset** values: `VITE_API_URL` →
`http://localhost:<API_PORT>`, and `VITE_SUPABASE_URL` / keys → the offset
Supabase api port. Vite must read its port from env (e.g.
`port: Number(process.env.VITE_DEV_PORT) || 5173` in `vite.config.ts`, with
`dev.sh` exporting `VITE_DEV_PORT`). CORS already allows any `http://localhost:\d+`,
so no API change there.

### CI is unaffected (verified)

`supabase db push` runs **only** in CI (`.github/workflows/supabase.yml`, on push
to `main` touching `supabase/**`). It links by **explicit ref**
(`supabase link --project-ref "$SUPABASE_PROJECT_REF"`, ref = `amhkpsrvjlthmmemwssc`
from a workflow env var), not from `config.toml`'s `project_id`. CI also checks
out clean `main` and never sets `HEX_INSTANCE`. So per-worktree `project_id` /
port overrides cannot affect the remote push target. There is no local link state
to worry about.

### UNSOLVED — verify before building: does `config.toml` `env()` interpolation cover integer port fields?

The intended mechanism is Supabase's `env(VAR)` substitution in `config.toml`, so
`HEX_INSTANCE`-derived values (ports + `project_id` + the lean `enabled` flags)
are injected at `supabase start` while the file stays git-tracked. **Open
question: does `env()` work on the numeric `port = …` fields, or string values
only?** `HEX_INSTANCE` *must* drive the ports, so if `env()` chokes on numeric
fields the pure-env() approach is dead for the thing that matters most.

- Verify empirically (the docs are ambiguous on numeric fields): render a
  throwaway `config.toml` with `port = env(SOME_VAR)` in a scratch dir and have
  the CLI parse it in isolation — do **not** disturb a running stack.
- **Fallback if env() can't do ports:** generated-template approach — track
  `config.toml.template`, gitignore the rendered `config.toml`, and have `dev.sh`
  render it per-worktree (ports + `project_id` + `enabled` flags in one pass). CI
  renders/uses the committed default and is still unaffected. The user-facing
  design is identical; only the rendering mechanism differs.
