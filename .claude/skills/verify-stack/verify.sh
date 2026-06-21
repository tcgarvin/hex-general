#!/usr/bin/env bash
# Smoke-test the deployed hex-general stack: browser -> Cloud Run -> Supabase.
# Exits non-zero if any check fails. See SKILL.md for what each check means.
set -uo pipefail

API="${HEX_API_URL:-https://hex-api-qfzj5tg56a-uc.a.run.app}"
WEB="${HEX_WEB_URL:-https://hex-general.vercel.app}"
ORIGIN="${HEX_WEB_ORIGIN:-$WEB}"
API_HOST="${API#*://}"; API_HOST="${API_HOST%%/*}"

fail=0
pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }

# Extract a response header value (case-insensitive) for a GET with an Origin.
# No -f, so headers come through even on a 5xx (CORS headers are still set).
acao_for() {
  curl -sS -D - -o /dev/null -H "Origin: $1" "$API/stats" 2>/dev/null \
    | tr -d '\r' \
    | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2}'
}

echo "API: $API"
echo "WEB: $WEB"
echo

echo "[api] liveness & version"
curl -fsS "$API/health" 2>/dev/null | grep -q '"status":"ok"' \
  && pass "/health ok" || bad "/health did not return ok"
ver=$(curl -fsS "$API/version" 2>/dev/null)
echo "$ver" | grep -q '"version"' \
  && pass "/version: $ver" || bad "/version missing version"

echo "[api] /stats (Cloud Run -> Supabase)"
stats=$(curl -fsS "$API/stats" 2>/dev/null)
if echo "$stats" | jq -e '(.active_users|type) == "number"' >/dev/null 2>&1; then
  pass "/stats -> $stats"
else
  bad "/stats not valid stats JSON (got: ${stats:-<empty/HTTP error>}); likely a missing service_role GRANT — check Cloud Run logs"
fi

echo "[api] CORS"
got=$(acao_for "$ORIGIN")
[ "$got" = "$ORIGIN" ] \
  && pass "allowed origin echoed ($ORIGIN)" \
  || bad "ACAO for $ORIGIN was '${got:-<none>}' (expected $ORIGIN)"
evil=$(acao_for "https://evil.example.com")
[ -z "$evil" ] \
  && pass "disallowed origin rejected" \
  || bad "disallowed origin got ACAO '$evil'"

echo "[web] /stats page & bundle"
code=$(curl -fsS -o /dev/null -w '%{http_code}' "$WEB/stats" 2>/dev/null)
[ "$code" = "200" ] && pass "$WEB/stats -> 200" || bad "$WEB/stats -> ${code:-<error>}"

html=$(curl -fsS "$WEB/stats" 2>/dev/null)
chunks=$(echo "$html" | grep -oE '/_next/static/chunks/[^"]+\.js' | sort -u)
found_rel=0; found_abs=0
for c in $chunks; do
  body=$(curl -fsS "$WEB$c" 2>/dev/null)
  echo "$body" | grep -q 'fetch("/stats")' && found_rel=1
  echo "$body" | grep -qF "$API_HOST" && found_abs=1
done
if [ "$found_rel" = "1" ]; then
  bad "bundle uses relative fetch(\"/stats\") — NEXT_PUBLIC_API_URL inlined empty; use || not ?? in src/lib/api.ts"
elif [ "$found_abs" = "1" ]; then
  pass "bundle references the absolute API host ($API_HOST)"
else
  bad "could not find the /stats fetch in the bundle (chunk names changed? inspect manually)"
fi

echo
if [ "$fail" = "0" ]; then
  printf '\033[32mAll stack checks passed.\033[0m\n'
else
  printf '\033[31mStack checks FAILED.\033[0m\n'
fi
exit "$fail"
