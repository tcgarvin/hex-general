#!/usr/bin/env bash
#
# Local game-state setup for hex-general — mint a dev token and drive the game
# lifecycle over the API, so an agent (or a human) can clear/start a game in one
# shot instead of clicking through the UI. Talks to *this worktree's* local stack
# only: it reads the API/Supabase URLs + anon key the dev loop wrote to
# frontend/.env.local, and authenticates as a seeded @hex.local dev account
# (password from api/app/dev_seed.py). It never targets the deployed project.
#
# Used by the `game-pilot` subagent for state setup before it screenshots the UI;
# handy ad hoc too. Requires the local stack to be up (`scripts/dev.sh up`).
#
#   scripts/game-setup.sh active [email]                caller's active game (JSON)
#   scripts/game-setup.sh history [email]               caller's game history (JSON)
#   scripts/game-setup.sh new [--bot P] [--scenario S] [email]   start a vs-bot game
#   scripts/game-setup.sh clear [email]                 resign the active game (if any)
#   scripts/game-setup.sh reset [--bot P] [--scenario S] [email] clear + new (fresh board)
#   scripts/game-setup.sh token [email]                 print a bearer token (for curl)
#
# --bot is the bot_policy (default: scripted; the other authored one is `search`).
# --scenario defaults to the API's default (twin_rivers). email defaults to
# dev1@hex.local; dev2@hex.local is side B. On `new`/`reset` it prints the created
# game's JSON (includes game_id) to stdout; diagnostics go to stderr.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FE_ENV="$REPO/frontend/.env.local"
SEED_PY="$REPO/api/app/dev_seed.py"
DEFAULT_EMAIL="dev1@hex.local"

die() { echo "game-setup: $*" >&2; exit 1; }

# --- config from the dev loop's rendered env (never hardcode ports/keys) -------
[ -f "$FE_ENV" ] || die "missing $FE_ENV — is the local stack up? run scripts/dev.sh up"
read_env() { sed -n "s/^$1=//p" "$FE_ENV" | head -1; }
API="$(read_env VITE_API_URL)"
SB="$(read_env VITE_SUPABASE_URL)"
ANON="$(read_env VITE_SUPABASE_ANON_KEY)"
[ -n "$API" ] && [ -n "$SB" ] && [ -n "$ANON" ] \
  || die "could not read VITE_API_URL / VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY from $FE_ENV"

# Password is a documented LOCAL fixture; read it from the seed module so this
# stays in sync if it ever changes (fall back to the current known value).
DEV_PASSWORD="$(sed -n 's/^DEV_PASSWORD = "\([^"]*\)"/\1/p' "$SEED_PY" | head -1)"
DEV_PASSWORD="${DEV_PASSWORD:-devpass123}"

# --- small helpers -------------------------------------------------------------
# Extract one field from JSON on stdin; second arg is a default if null/absent.
json_get() { python3 -c 'import sys,json;d=json.load(sys.stdin);v=d
for k in sys.argv[1].split("."):
    v=v.get(k) if isinstance(v,dict) else None
print(v if v not in (None,"") else (sys.argv[2] if len(sys.argv)>2 else ""))' "$1" "${2-}"; }

mint_token() {  # $1 = email
  local email="$1" resp token
  resp="$(curl -s "$SB/auth/v1/token?grant_type=password" \
    -H "apikey: $ANON" -H "Content-Type: application/json" \
    -d "{\"email\":\"$email\",\"password\":\"$DEV_PASSWORD\"}")"
  token="$(printf '%s' "$resp" | json_get access_token)"
  [ -n "$token" ] || die "login failed for $email — $(printf '%s' "$resp" | json_get error_description "auth error")"
  printf '%s' "$token"
}

api_get()  { curl -s "$API$1" -H "Authorization: Bearer $TOKEN"; }
api_post() {  # $1 = path, $2 = JSON body (defaults to an empty object)
  local body="${2:-}"; [ -n "$body" ] || body='{}'
  curl -s -X POST "$API$1" -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "$body"
}

active_game_id() { api_get /games/active | json_get active_game.game_id; }

do_clear() {  # resign the active game if there is one; no-op otherwise
  local gid; gid="$(active_game_id)"
  if [ -n "$gid" ]; then
    api_post "/games/$gid/resign" >/dev/null
    echo "cleared active game $gid" >&2
  else
    echo "no active game to clear" >&2
  fi
}

do_new() {  # create a vs-bot game; prints the create response JSON
  local body; body="$(python3 -c 'import json,sys;print(json.dumps({"vs_bot":True,"bot_policy":sys.argv[1],"scenario":sys.argv[2]} if sys.argv[2] else {"vs_bot":True,"bot_policy":sys.argv[1]}))' "$BOT" "$SCENARIO")"
  local resp; resp="$(api_post /games "$body")"
  local gid; gid="$(printf '%s' "$resp" | json_get game_id)"
  [ -n "$gid" ] || die "create failed — $(printf '%s' "$resp" | json_get detail "$resp")"
  echo "started vs-bot($BOT) game $gid" >&2
  printf '%s\n' "$resp" | python3 -m json.tool
}

# --- arg parsing ---------------------------------------------------------------
CMD="${1:-}"; shift || true
BOT="scripted"; SCENARIO=""; EMAIL="$DEFAULT_EMAIL"
while [ $# -gt 0 ]; do
  case "$1" in
    --bot)      BOT="${2:?--bot needs a value}"; shift 2 ;;
    --scenario) SCENARIO="${2:?--scenario needs a value}"; shift 2 ;;
    -*)         die "unknown option: $1" ;;
    *)          EMAIL="$1"; shift ;;
  esac
done

case "$CMD" in
  token)   mint_token "$EMAIL"; echo ;;
  active)  TOKEN="$(mint_token "$EMAIL")"; api_get /games/active   | python3 -m json.tool ;;
  history) TOKEN="$(mint_token "$EMAIL")"; api_get /games/history  | python3 -m json.tool ;;
  clear)   TOKEN="$(mint_token "$EMAIL")"; do_clear ;;
  new)     TOKEN="$(mint_token "$EMAIL")"; do_new ;;
  reset)   TOKEN="$(mint_token "$EMAIL")"; do_clear; do_new ;;
  ""|-h|--help|help)
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
  *) die "unknown command: $CMD (try: active history new clear reset token)" ;;
esac
