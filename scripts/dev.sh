#!/usr/bin/env bash
#
# Run the two local dev servers detached, with their output going to log FILES
# rather than a terminal (or an agent's context). The main Claude agent should
# never babysit streaming server logs — it runs `up` and moves on, then spawns
# the `dev-doctor` subagent (.claude/agents/dev-doctor.md) on demand to read
# these logs / health-check and hand back a compact verdict.
#
#   - API:      cd api && uv run uvicorn app.main:app --reload --port 8080
#   - Frontend: cd frontend && npm run dev   (Vite, :5173)
#
# Prereq: ./scripts/dev-up.sh has already brought up the local Supabase stack
# and written api/.env + frontend/.env.local. This script does NOT manage
# Supabase or env — only the two app servers. See docs/LOCAL_DEV.md.
#
# Usage:
#   scripts/dev.sh up                  start both (idempotent), wait for health
#   scripts/dev.sh down                stop both
#   scripts/dev.sh status              one line of health per server + recent errors
#   scripts/dev.sh logs [api|frontend] [N]   print last N log lines (default 60)
#
# State lives in .dev/ (gitignored): <name>.pid and <name>.log per server.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_DIR="$REPO/.dev"
mkdir -p "$DEV_DIR"

API_PORT=8080
FE_PORT=5173
API_HEALTH="http://localhost:$API_PORT/health"
FE_HEALTH="http://localhost:$FE_PORT/"

pidfile() { echo "$DEV_DIR/$1.pid"; }
logfile() { echo "$DEV_DIR/$1.log"; }

# Is the recorded pid for $1 a live process?
running() {
  local pf; pf="$(pidfile "$1")"
  [ -f "$pf" ] || return 1
  local pid; pid="$(cat "$pf" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# health_ok URL -> 0 if a request succeeds (any HTTP response counts as "the
# port is serving"); curl -f would reject non-2xx, but Vite's root and the API's
# /health both return 2xx, so -f is the stricter, correct check here.
health_ok() { curl -fsS -o /dev/null --max-time 2 "$1" 2>/dev/null; }

# start_server NAME "CMD" CWD HEALTH_URL
start_server() {
  local name="$1" cmd="$2" cwd="$3" url="$4"
  local pf lf; pf="$(pidfile "$name")"; lf="$(logfile "$name")"

  if running "$name"; then
    echo "  $name: already running (pid $(cat "$pf"))"
    return 0
  fi

  # Run in a new session/process group (setsid) so (a) the server survives this
  # shell and the agent's Bash call exiting, and (b) `down` can signal the whole
  # group. The group matters because `npm run dev` does NOT forward SIGTERM to
  # its Vite child — killing only the leader would orphan Vite and leak :5173.
  # In a non-interactive shell setsid doesn't fork, so $! is the group leader
  # (pgid == pid). stdin from /dev/null detaches from any terminal.
  : >"$lf"
  setsid bash -c "cd '$cwd' && exec $cmd" >"$lf" 2>&1 </dev/null &
  local pid=$!
  echo "$pid" >"$pf"
  disown "$pid" 2>/dev/null || true
  echo "  $name: starting (pid $pid) -> ${lf#$REPO/}"

  # Wait up to ~30s for it to answer; bail early if the process dies.
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
  # Signal the whole process group (negative pid) so Vite/esbuild/the uvicorn
  # worker go down with the leader. Fall back to the bare pid if the group send
  # fails (e.g. leader already gone).
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
  elif [ -f "$lf" ] && lsof -ti ":$port" >/dev/null 2>&1; then
    state="DOWN (no tracked pid, but :$port is bound by another process)"
  else
    state="down"
  fi
  printf '  %-9s (:%s): %s\n' "$name" "$port" "$state"

  # On trouble, surface the last error-ish lines so the doctor/agent has a lead.
  if [[ "$state" != up* ]] && [ -f "$lf" ]; then
    local errs
    errs="$(grep -iE 'error|traceback|exception|EADDRINUSE|address already in use' "$lf" 2>/dev/null | tail -3)"
    [ -n "$errs" ] && echo "$errs" | sed 's/^/      /'
  fi
}

cmd_up() {
  if [ ! -f "$REPO/api/.env" ]; then
    echo "warning: api/.env is missing — run ./scripts/dev-up.sh first (it brings up Supabase and writes env)." >&2
  fi
  echo "Starting local dev servers..."
  local rc=0
  start_server api "uv run uvicorn app.main:app --reload --port $API_PORT" "$REPO/api" "$API_HEALTH" || rc=1
  start_server frontend "npm run dev" "$REPO/frontend" "$FE_HEALTH" || rc=1
  echo
  echo "  App:    http://localhost:$FE_PORT"
  echo "  API:    http://localhost:$API_PORT  (docs at /docs)"
  echo "  Logs:   scripts/dev.sh logs [api|frontend]"
  echo "  Stop:   scripts/dev.sh down"
  return $rc
}

cmd_down() {
  echo "Stopping local dev servers..."
  stop_server api
  stop_server frontend
}

cmd_status() {
  status_server api "$API_HEALTH" "$API_PORT"
  status_server frontend "$FE_HEALTH" "$FE_PORT"
}

cmd_logs() {
  local name="${1:-}" n="${2:-60}"
  if [ -z "$name" ]; then
    echo "usage: scripts/dev.sh logs [api|frontend] [N]" >&2
    return 2
  fi
  local lf; lf="$(logfile "$name")"
  if [ ! -f "$lf" ]; then
    echo "no log for '$name' (not started?). Expected ${lf#$REPO/}" >&2
    return 1
  fi
  tail -n "$n" "$lf"
}

case "${1:-}" in
  up)     shift; cmd_up "$@" ;;
  down)   shift; cmd_down "$@" ;;
  status) shift; cmd_status "$@" ;;
  logs)   shift; cmd_logs "$@" ;;
  restart) shift; cmd_down; cmd_up "$@" ;;
  *)
    echo "usage: scripts/dev.sh {up|down|restart|status|logs [api|frontend] [N]}" >&2
    exit 2
    ;;
esac
