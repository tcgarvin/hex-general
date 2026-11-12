#!/usr/bin/env bash
#
# Local world setup for hex-general — mint a dev token and drive world games over
# the API, so an agent (or a human) can clear/create/join/tick a world in one shot
# instead of clicking through the UI. Talks to *this worktree's* local stack only:
# it reads the API/Supabase URLs + anon key the dev loop wrote to
# frontend/.env.local, and authenticates as a seeded @hex.local dev account
# (password from api/app/dev_seed.py). It never targets the deployed project.
#
# Used by the `game-pilot` subagent for state setup before it screenshots the UI;
# handy ad hoc too. Requires the local stack to be up (`scripts/dev.sh up`).
#
#   scripts/game-setup.sh active [email]                 caller's active world (JSON)
#   scripts/game-setup.sh create-world [--slots N] [--bots N] [--seed N]
#                         [--clock manual|twice_daily] [email]
#                                                        create a world (admin API route)
#   scripts/game-setup.sh join <game_id> [email]         seat the caller in a world
#   scripts/game-setup.sh tick <game_id> [email]         resolve a world's turn now (admin)
#   scripts/game-setup.sh clear [email]                  delete the caller's active world
#   scripts/game-setup.sh delete-world <game_id> [email] delete a world outright (admin)
#   scripts/game-setup.sh token [email]                  print a bearer token (for curl)
#
# `clear` and `delete-world` exit non-zero if the account still has an active world.
# `create-world` defaults: --slots 8 --bots 4 --seed 0 --clock manual; it prints the
# created world's JSON (includes game_id) to stdout and does NOT seat the caller
# (follow with `join`). `join` and `tick` print the API response JSON. The admin
# routes need an admin (every dev is one; the seeded @hex.local accounts are devs
# locally).
# email defaults to dev1@hex.local; diagnostics go to stderr.
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

active_world_id() { api_get /worlds/active | json_get active_world.game_id; }

delete_world() {  # $1 = world game id; admin route, deletes game + map
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/dev/worlds/$1" \
    -H "Authorization: Bearer $TOKEN")"
  [ "$code" = "204" ] || die "delete world $1 failed (HTTP $code)"
  echo "deleted world $1" >&2
}

require_no_active_world() {
  local left; left="$(active_world_id)"
  [ -z "$left" ] || die "account still has active world $left after clearing"
}

do_clear() {  # delete the caller's active world if there is one
  local gid; gid="$(active_world_id)"
  if [ -z "$gid" ]; then
    echo "no active world to clear" >&2
    return
  fi
  delete_world "$gid"
  require_no_active_world
}

# POST $1 with body $2; print the JSON response, or die with its detail unless it
# carries the field $3 (the success marker).
post_or_die() {
  local resp; resp="$(api_post "$1" "${2:-}")"
  [ -n "$(printf '%s' "$resp" | json_get "$3")" ] \
    || die "POST $1 failed — $(printf '%s' "$resp" | json_get detail "$resp")"
  printf '%s\n' "$resp" | python3 -m json.tool
}

do_create_world() {
  local body
  body="$(python3 -c 'import json,sys
slots,bots,seed,clock=sys.argv[1:]
print(json.dumps({"slots":int(slots),"bots":int(bots),"seed":int(seed),"clock":clock}))' \
    "$SLOTS" "$BOTS" "$SEED" "$CLOCK")" || die "--slots/--bots/--seed must be integers"
  post_or_die /dev/worlds "$body" game_id
  echo "created world ($SLOTS slots, $BOTS bots, seed $SEED, $CLOCK clock)" >&2
}

# --- arg parsing ---------------------------------------------------------------
CMD="${1:-}"; shift || true
SLOTS=8; BOTS=4; SEED=0; CLOCK="manual"; WORLD_ID=""; EMAIL="$DEFAULT_EMAIL"
while [ $# -gt 0 ]; do
  case "$1" in
    --slots) SLOTS="${2:?--slots needs a value}"; shift 2 ;;
    --bots)  BOTS="${2:?--bots needs a value}"; shift 2 ;;
    --seed)  SEED="${2:?--seed needs a value}"; shift 2 ;;
    --clock)
      CLOCK="${2:?--clock needs a value}"; shift 2
      case "$CLOCK" in manual|twice_daily) ;; *) die "--clock must be manual or twice_daily" ;; esac ;;
    -*)      die "unknown option: $1" ;;
    *)
      case "$CMD" in
        join|tick|delete-world) if [ -z "$WORLD_ID" ]; then WORLD_ID="$1"; else EMAIL="$1"; fi ;;
        *) EMAIL="$1" ;;
      esac
      shift ;;
  esac
done

need_world_id() { [ -n "$WORLD_ID" ] || die "usage: game-setup.sh $CMD <game_id> [email]"; }

case "$CMD" in
  token)   mint_token "$EMAIL"; echo ;;
  active)  TOKEN="$(mint_token "$EMAIL")"; api_get /worlds/active | python3 -m json.tool ;;
  create-world) TOKEN="$(mint_token "$EMAIL")"; do_create_world ;;
  join)
    need_world_id; TOKEN="$(mint_token "$EMAIL")"
    post_or_die "/worlds/$WORLD_ID/join" '' slot ;;
  tick)
    need_world_id; TOKEN="$(mint_token "$EMAIL")"
    post_or_die "/dev/worlds/$WORLD_ID/tick" '' turn ;;
  clear)   TOKEN="$(mint_token "$EMAIL")"; do_clear ;;
  delete-world)
    need_world_id; TOKEN="$(mint_token "$EMAIL")"; delete_world "$WORLD_ID"
    [ "$(active_world_id)" != "$WORLD_ID" ] || die "account still in world $WORLD_ID" ;;
  ""|-h|--help|help)
    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
  *) die "unknown command: $CMD (try: active create-world join tick clear delete-world token)" ;;
esac
