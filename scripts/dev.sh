#!/usr/bin/env bash
#
# Per-worktree local dev lifecycle — one entrypoint for the whole stack:
# an isolated Supabase stack + the two app servers (+ an on-demand montage
# server), all offset by an instance number so multiple git worktrees can run
# side by side without colliding on ports or Docker containers.
#
#   scripts/dev.sh up                 bring the worktree's stack up (idempotent)
#   scripts/dev.sh down               tear it down
#   scripts/dev.sh status             health of every piece (+ recent errors)
#   scripts/dev.sh logs [name] [N]    tail a server log (api|frontend|montage)
#   scripts/dev.sh montage up|down    the tile-montage http server (on demand)
#   scripts/dev.sh restart            down + up
#
# HEX_INSTANCE (integer ≥ 0) drives every port with a stride of 100:
#   app API  8080+100N   Vite      5173+100N   montage   8011+100N
#   sb api  54321+100N   sb db    54322+100N   (+ shadow/pooler/studio/inbucket/
#   analytics/edge inspector, all offset in the rendered supabase config)
# Unset → the first free slot is auto-allocated and recorded in .dev/instance
# (gitignored). Set it explicitly to pin a worktree to a slot.
#
# The stack is LEAN by default (studio/realtime/inbucket/analytics off — the app
# only needs Auth + Postgres locally). `HEX_SUPABASE_FULL=1 scripts/dev.sh up`
# brings those back for this worktree.
#
# State lives in .dev/ (gitignored): instance, <name>.pid, <name>.log, and the
# rendered supabase workdir under .dev/supabase/. See docs/LOCAL_DEV.md.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="$REPO/.dev"
SB_WORKDIR="$DEV_DIR"                       # supabase --workdir root (.dev/supabase/…)
SB_DIR="$DEV_DIR/supabase"
MONTAGE_DIR="$DEV_DIR/montages"
INSTANCE_FILE="$DEV_DIR/instance"
API_ENV="$REPO/api/.env"
FE_ENV="$REPO/frontend/.env.local"
BASE_CONFIG="$REPO/supabase/config.toml"
SIGNING_KEY="$SB_DIR/signing_keys.json"
MAX_INSTANCE=40

mkdir -p "$DEV_DIR"

# --- locate the Supabase CLI (mirrors the old dev-up.sh) ---
if command -v supabase >/dev/null 2>&1; then
  SUPABASE="supabase"
elif [ -x "$HOME/.local/bin/supabase" ]; then
  SUPABASE="$HOME/.local/bin/supabase"
else
  SUPABASE=""
fi

pidfile() { echo "$DEV_DIR/$1.pid"; }
logfile() { echo "$DEV_DIR/$1.log"; }

# ── port math ───────────────────────────────────────────────────────────────
# Only the host ports we bind directly need computing here; every other supabase
# port is offset inside the rendered config by render-dev-config.py.
app_api_port() { echo $((8080 + 100 * $1)); }
vite_port()    { echo $((5173 + 100 * $1)); }
montage_port() { echo $((8011 + 100 * $1)); }
sb_api_port()  { echo $((54321 + 100 * $1)); }

# A port is "free" if nothing is listening on it. The connect probe runs in a
# SUBSHELL: a failed /dev/tcp redirection on a bare `exec` exits the shell, and
# we want that to kill only the probe, not dev.sh.
port_free() {
  local p="$1"
  (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && return 1
  return 0
}

# All host ports this instance would bind are free?
instance_free() {
  local n="$1"
  port_free "$(app_api_port "$n")" && port_free "$(vite_port "$n")" \
    && port_free "$(sb_api_port "$n")"
}

allocate_instance() {
  local n
  for n in $(seq 0 "$MAX_INSTANCE"); do
    if instance_free "$n"; then echo "$n"; return 0; fi
  done
  echo "error: no free dev instance slot in 0..$MAX_INSTANCE" >&2
  return 1
}

# Are any of this worktree's own servers running? (used to tell "our slot" from
# "someone else's" during reallocation).
running_any() { running api || running frontend || running montage; }

# Resolve the instance for a lifecycle command.
#   up:  HEX_INSTANCE (strict) > .dev/instance (reallocated if its ports are
#        taken and none of our servers are up) > auto-allocate. Persists it.
#   others: HEX_INSTANCE > .dev/instance > 0 (nothing to persist).
resolve_instance() {
  local mode="$1" n
  if [ -n "${HEX_INSTANCE:-}" ]; then
    if ! [[ "$HEX_INSTANCE" =~ ^[0-9]+$ ]]; then
      echo "error: HEX_INSTANCE must be a non-negative integer (got '$HEX_INSTANCE')" >&2
      return 1
    fi
    n="$HEX_INSTANCE"
  elif [ -f "$INSTANCE_FILE" ]; then
    n="$(cat "$INSTANCE_FILE" 2>/dev/null)"
  else
    n=""
  fi

  if [ "$mode" = up ]; then
    if [ -z "$n" ]; then
      n="$(allocate_instance)" || return 1
    elif [ -z "${HEX_INSTANCE:-}" ] && ! running_any && ! instance_free "$n"; then
      # Our recorded slot got taken by another worktree while we were down.
      local fresh; fresh="$(allocate_instance)" || return 1
      echo "note: instance $n is busy; reallocating this worktree to $fresh" >&2
      n="$fresh"
    fi
    echo "$n" >"$INSTANCE_FILE"
  fi
  [ -n "$n" ] || n=0
  echo "$n"
}

# ── generic server supervision (unchanged shape from the old dev.sh) ─────────
running() {
  local pf; pf="$(pidfile "$1")"
  [ -f "$pf" ] || return 1
  local pid; pid="$(cat "$pf" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

health_ok() { curl -fsS -o /dev/null --max-time 2 "$1" 2>/dev/null; }

# start_server NAME "CMD" CWD HEALTH_URL
start_server() {
  local name="$1" cmd="$2" cwd="$3" url="$4"
  local pf lf; pf="$(pidfile "$name")"; lf="$(logfile "$name")"

  if running "$name"; then
    echo "  $name: already running (pid $(cat "$pf"))"
    return 0
  fi

  # New session/process group (setsid) so the server survives this shell and
  # `down` can signal the whole group — `npm run dev` doesn't forward SIGTERM to
  # its Vite child, so killing only the leader would orphan Vite and leak its
  # port. In a non-interactive shell setsid doesn't fork, so $! is the group
  # leader. stdin from /dev/null detaches from any terminal.
  : >"$lf"
  setsid bash -c "cd '$cwd' && exec $cmd" >"$lf" 2>&1 </dev/null &
  local pid=$!
  echo "$pid" >"$pf"
  disown "$pid" 2>/dev/null || true
  echo "  $name: starting (pid $pid) -> ${lf#$REPO/}"

  local i
  for i in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "  $name: EXITED during startup — see ${lf#$REPO/}" >&2
      rm -f "$pf"
      return 1
    fi
    if health_ok "$url"; then
      echo "  $name: healthy"
      return 0
    fi
    sleep 0.5
  done
  echo "  $name: started but not healthy after 30s — see ${lf#$REPO/}" >&2
  return 1
}

stop_server() {
  local name="$1" pf pid; pf="$(pidfile "$name")"
  if ! running "$name"; then
    echo "  $name: not running"
    rm -f "$pf"
    return 0
  fi
  pid="$(cat "$pf")"
  kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  local i
  for i in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "  $name: did not stop on SIGTERM, sending SIGKILL"
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pf"
  echo "  $name: stopped"
}

status_server() {
  local name="$1" url="$2" port="$3" lf; lf="$(logfile "$name")"
  local state
  if running "$name"; then
    if health_ok "$url"; then
      state="up (pid $(cat "$(pidfile "$name")"), healthy)"
    else
      state="UNHEALTHY (pid alive but :$port not answering)"
    fi
  elif [ -f "$lf" ] && ! port_free "$port"; then
    state="DOWN (no tracked pid, but :$port is bound by another process)"
  else
    state="down"
  fi
  printf '  %-9s (:%s): %s\n' "$name" "$port" "$state"

  if [[ "$state" != up* ]] && [ -f "$lf" ]; then
    local errs
    errs="$(grep -iE 'error|traceback|exception|EADDRINUSE|address already in use' "$lf" 2>/dev/null | tail -3)"
    [ -n "$errs" ] && echo "$errs" | sed 's/^/      /'
  fi
}

# ── supabase stack ───────────────────────────────────────────────────────────
require_supabase() {
  [ -n "$SUPABASE" ] || {
    echo "error: supabase CLI not found. Install: https://supabase.com/docs/guides/local-development" >&2
    return 1
  }
}

# Build .dev/supabase/ for the given instance: rendered config + a signing key +
# a symlink back to the real migrations. `--workdir .dev` then runs from here.
render_workdir() {
  local n="$1" project_id="$2" full_flag="$3"
  mkdir -p "$SB_DIR"
  python3 "$REPO/scripts/render-dev-config.py" \
    --src "$BASE_CONFIG" --instance "$n" --project-id "$project_id" \
    --vite-port "$(vite_port "$n")" $full_flag >"$SB_DIR/config.toml" || return 1

  # Migrations are the source of truth in supabase/migrations; link, don't copy.
  ln -sfn "$REPO/supabase/migrations" "$SB_DIR/migrations"

  # Throwaway local ES256 signing key (matches prod's asymmetric JWTs so the API
  # verifies local tokens unchanged). Path is relative to the config dir.
  if [ ! -s "$SIGNING_KEY" ]; then
    echo '[]' >"$SIGNING_KEY"
    if ! "$SUPABASE" gen signing-key --algorithm ES256 --append --workdir "$SB_WORKDIR" >/dev/null 2>&1; then
      rm -f "$SIGNING_KEY"
      echo "error: failed to generate local signing key" >&2
      return 1
    fi
  fi
}

# ── env files ────────────────────────────────────────────────────────────────
write_env_files() {
  local n="$1" status_env api_url anon service_role app_api
  app_api="$(app_api_port "$n")"

  status_env="$("$SUPABASE" status --workdir "$SB_WORKDIR" -o env 2>/dev/null)"
  # status -o env prints KEY="value" lines; pull the three we need.
  api_url="$(sed -n 's/^API_URL="\?\([^"]*\)"\?/\1/p' <<<"$status_env")"
  anon="$(sed -n 's/^ANON_KEY="\?\([^"]*\)"\?/\1/p' <<<"$status_env")"
  service_role="$(sed -n 's/^SERVICE_ROLE_KEY="\?\([^"]*\)"\?/\1/p' <<<"$status_env")"

  if [ -z "$api_url" ] || [ -z "$service_role" ] || [ -z "$anon" ]; then
    echo "error: could not read API_URL / SERVICE_ROLE_KEY / ANON_KEY from 'supabase status'." >&2
    return 1
  fi

  cat >"$API_ENV" <<EOF
# Generated by scripts/dev.sh (instance $n) for this worktree's local Supabase
# stack. Not committed; re-run scripts/dev.sh up to refresh. See docs/LOCAL_DEV.md.
SUPABASE_URL=$api_url
SUPABASE_SERVICE_ROLE_KEY=$service_role
EOF

  echo "Seeding dev accounts..."
  local seed_out dev_ids
  seed_out="$(cd "$REPO/api" && uv run python -m app.dev_seed)" || {
    echo "error: seeding failed:" >&2; echo "$seed_out" >&2; return 1
  }
  echo "$seed_out"
  dev_ids="$(grep -E '^DEV_USER_IDS=' <<<"$seed_out" | tail -1)"
  [ -n "$dev_ids" ] || { echo "error: seed did not emit a DEV_USER_IDS line." >&2; return 1; }
  echo "$dev_ids" >>"$API_ENV"

  # Point the frontend at this instance's stack (back up any real .env.local once).
  if [ -f "$FE_ENV" ] && [ ! -f "$FE_ENV.bak" ] && ! grep -q 'Generated by scripts/dev' "$FE_ENV"; then
    cp "$FE_ENV" "$FE_ENV.bak"
    echo "Backed up your existing frontend/.env.local -> frontend/.env.local.bak"
  fi
  cat >"$FE_ENV" <<EOF
# Generated by scripts/dev.sh (instance $n) — points the Vite app at this
# worktree's local stack. A prior .env.local (if any) is saved as .env.local.bak;
# scripts/dev.sh down restores it.
VITE_SUPABASE_URL=$api_url
VITE_SUPABASE_ANON_KEY=$anon
VITE_API_URL=http://localhost:$app_api
VITE_DEV_PORT=$(vite_port "$n")
EOF
}

# A fresh git worktree has no frontend/node_modules (gitignored, per-worktree),
# so `npm run dev` can't find vite. `uv run` auto-syncs the API venv, but npm
# won't — bootstrap it on first up. Output goes to a log, not this context.
ensure_frontend_deps() {
  [ -x "$REPO/frontend/node_modules/.bin/vite" ] && return 0
  echo "Installing frontend deps (first run in this worktree) -> .dev/npm-install.log"
  if ! (cd "$REPO/frontend" && npm install) >"$DEV_DIR/npm-install.log" 2>&1; then
    echo "error: npm install failed — see .dev/npm-install.log" >&2
    return 1
  fi
}

# ── commands ─────────────────────────────────────────────────────────────────
cmd_up() {
  require_supabase || return 1
  if ! docker info >/dev/null 2>&1; then
    echo "error: Docker is not running (or this user can't reach the daemon)." >&2
    echo "       Start it (e.g. 'sudo systemctl start docker') and ensure you're in the 'docker' group." >&2
    return 1
  fi

  local n; n="$(resolve_instance up)" || return 1
  local hash project_id full_flag=""
  hash="$(printf '%s' "$REPO" | sha256sum | cut -c1-8)"
  project_id="hex_${hash}"
  local mode="lean"
  if [ "${HEX_SUPABASE_FULL:-0}" = 1 ]; then full_flag="--full"; mode="FULL"; fi

  echo "== dev instance $n ($project_id, $mode) =="
  echo "Rendering supabase workdir + starting stack (first run pulls images)..."
  render_workdir "$n" "$project_id" "$full_flag" || return 1
  "$SUPABASE" start --workdir "$SB_WORKDIR" || {
    echo "error: 'supabase start' failed (see output above)." >&2; return 1
  }
  write_env_files "$n" || return 1

  ensure_frontend_deps || return 1

  echo "Starting app servers..."
  local rc=0
  start_server api "uv run uvicorn app.main:app --reload --port $(app_api_port "$n")" \
    "$REPO/api" "http://localhost:$(app_api_port "$n")/health" || rc=1
  # Vite reads its port from VITE_DEV_PORT (see vite.config.ts). Pass it via
  # `env` — start_server `exec`s the command, and exec takes no assignment prefix
  # (`exec VAR=x cmd` looks for a program literally named "VAR=x").
  start_server frontend "env VITE_DEV_PORT=$(vite_port "$n") npm run dev" \
    "$REPO/frontend" "http://localhost:$(vite_port "$n")/" || rc=1

  local studio_line
  if [ "$mode" = FULL ]; then
    studio_line="http://localhost:$((54323 + 100 * n))"
  else
    studio_line="disabled (HEX_SUPABASE_FULL=1 to enable)"
  fi
  cat <<EOF

  App:    http://localhost:$(vite_port "$n")     (sign in dev1@hex.local — pw in api/app/dev_seed.py)
  API:    http://localhost:$(app_api_port "$n")     (docs at /docs)
  Studio: $studio_line
  Logs:   scripts/dev.sh logs [api|frontend]
  Stop:   scripts/dev.sh down
EOF
  return $rc
}

cmd_down() {
  local n; n="$(resolve_instance down)"
  echo "== stopping dev instance $n =="
  stop_server api
  stop_server frontend
  stop_server montage
  if [ -f "$SB_DIR/config.toml" ] && [ -n "$SUPABASE" ]; then
    echo "Stopping supabase stack..."
    "$SUPABASE" stop --workdir "$SB_WORKDIR" 2>/dev/null \
      || echo "  (supabase stack was not running)"
  fi
  if [ -f "$FE_ENV.bak" ]; then
    mv "$FE_ENV.bak" "$FE_ENV"
    echo "Restored frontend/.env.local from .env.local.bak"
  fi
  echo "Down. (.dev/instance kept so this worktree reuses slot $n; api/.env left in place.)"
}

cmd_status() {
  local n; n="$(resolve_instance status)"
  echo "== dev instance $n =="
  status_server api      "http://localhost:$(app_api_port "$n")/health" "$(app_api_port "$n")"
  status_server frontend "http://localhost:$(vite_port "$n")/"          "$(vite_port "$n")"
  status_server montage  "http://localhost:$(montage_port "$n")/"       "$(montage_port "$n")"
  if [ -n "$SUPABASE" ] && [ -f "$SB_DIR/config.toml" ]; then
    if "$SUPABASE" status --workdir "$SB_WORKDIR" >/dev/null 2>&1; then
      printf '  %-9s (:%s): up\n' "supabase" "$(sb_api_port "$n")"
    else
      printf '  %-9s (:%s): down\n' "supabase" "$(sb_api_port "$n")"
    fi
  fi
}

cmd_logs() {
  local name="${1:-}" nlines="${2:-60}"
  if [ -z "$name" ]; then
    echo "usage: scripts/dev.sh logs [api|frontend|montage] [N]" >&2
    return 2
  fi
  local lf; lf="$(logfile "$name")"
  if [ ! -f "$lf" ]; then
    echo "no log for '$name' (not started?). Expected ${lf#$REPO/}" >&2
    return 1
  fi
  tail -n "$nlines" "$lf"
}

# On-demand tile-montage http server (serves .dev/montages/). Kept out of `up`
# because it's only for board-tile review. See CLAUDE.md hex-art sign-off note.
cmd_montage() {
  local sub="${1:-}" n; n="$(resolve_instance status)"
  local port; port="$(montage_port "$n")"
  mkdir -p "$MONTAGE_DIR"
  case "$sub" in
    up)
      start_server montage \
        "python3 -m http.server $port --bind 127.0.0.1 --directory '$MONTAGE_DIR'" \
        "$REPO" "http://localhost:$port/"
      echo "  Montage server: http://127.0.0.1:$port/  (docroot ${MONTAGE_DIR#$REPO/})"
      echo "  Render into it: python3 scripts/tile-montage.py <dir|globs>  (defaults --out there)"
      ;;
    down) stop_server montage ;;
    ""|status)
      status_server montage "http://localhost:$port/" "$port" ;;
    *)
      echo "usage: scripts/dev.sh montage {up|down|status}" >&2; return 2 ;;
  esac
}

case "${1:-}" in
  up)      shift; cmd_up "$@" ;;
  down)    shift; cmd_down "$@" ;;
  restart) shift; cmd_down; echo; cmd_up "$@" ;;
  status)  shift; cmd_status "$@" ;;
  logs)    shift; cmd_logs "$@" ;;
  montage) shift; cmd_montage "$@" ;;
  *)
    echo "usage: scripts/dev.sh {up|down|restart|status|logs [name] [N]|montage up|down}" >&2
    exit 2
    ;;
esac
