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
# Capture then match with a herestring (not a pipe): a piped `grep -q` exits on
# its first match and SIGPIPEs the producer, which under `pipefail` turns a
# healthy response into a false failure on large inputs. See the bundle check below.
health=$(curl -fsS "$API/health" 2>/dev/null)
grep -q '"status":"ok"' <<<"$health" \
  && pass "/health ok" || bad "/health did not return ok"
ver=$(curl -fsS "$API/version" 2>/dev/null)
grep -q '"version"' <<<"$ver" \
  && pass "/version: $ver" || bad "/version missing version"

# World build (exists only after the 2026-11-10 cutover; the old API 404s these).
# The image is built from the head of the push that last deployed the API, so the
# live commit must contain the last commit that touched the API and must itself
# be in local history (a fixtures-only or docs-only push does not redeploy).
# HEX_EXPECT_COMMIT demands one exact commit instead.
echo "[api] world build"
repo="$(dirname "$0")"
commit=$(jq -r '.commit // empty' <<<"$ver" 2>/dev/null)
last_api=$(git -C "$repo" rev-list -1 HEAD -- ":(top)api" ":(top).github/workflows/api.yml" 2>/dev/null)
if [ -n "${HEX_EXPECT_COMMIT:-}" ]; then
  [ "$commit" = "$HEX_EXPECT_COMMIT" ] \
    && pass "/version commit is $HEX_EXPECT_COMMIT" \
    || bad "/version commit '${commit:-<none>}' is not $HEX_EXPECT_COMMIT"
elif [ -z "$commit" ] || [ -z "$last_api" ]; then
  bad "cannot compare commits (/version commit '${commit:-<none>}', last API commit '${last_api:-<none>}')"
elif ! git -C "$repo" cat-file -e "$commit^{commit}" 2>/dev/null; then
  bad "/version commit $commit is not in local history (fetch, or a build from elsewhere)"
elif git -C "$repo" merge-base --is-ancestor "$last_api" "$commit" \
  && git -C "$repo" merge-base --is-ancestor "$commit" HEAD; then
  pass "/version commit $commit includes the last API change ($last_api)"
else
  bad "/version commit $commit does not include the last API change $last_api (old build still serving, or deploy not done)"
fi
code=$(curl -sS -o /dev/null -w '%{http_code}' "$API/worlds" 2>/dev/null)
[ "$code" = "401" ] \
  && pass "GET /worlds without a token -> 401" \
  || bad "GET /worlds without a token -> ${code:-<error>} (expected 401; 404 means the pre-world API)"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/internal/clock" 2>/dev/null)
[ "$code" = "401" ] \
  && pass "POST /internal/clock without a token -> 401" \
  || bad "POST /internal/clock without a token -> ${code:-<error>} (expected 401; 404 means the pre-world API)"
# A missing token is refused before the clock config is read, so only a
# (malformed) token shows whether CLOCK_AUDIENCE / CLOCK_INVOKER_EMAIL are set:
# 403 = configured and the token rejected, 503 = the clock env vars are missing.
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/internal/clock" \
  -H "Authorization: Bearer not-a-real-token" 2>/dev/null)
case "$code" in
  403) pass "POST /internal/clock with a bad token -> 403 (clock configured)" ;;
  503) bad "POST /internal/clock -> 503: CLOCK_AUDIENCE / CLOCK_INVOKER_EMAIL not set on the service" ;;
  *)   bad "POST /internal/clock with a bad token -> ${code:-<error>} (expected 403)" ;;
esac

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

# Vite SPA: index.html (served for every route via the SPA rewrite) loads
# /assets/index-<hash>.js. The build inlines VITE_API_URL as a string literal, so
# the absolute API host must appear in the bundle. If it does not, VITE_API_URL
# was inlined empty (use || not ?? in src/lib/api/client.ts) or the build is stale.
html=$(curl -fsS "$WEB/stats" 2>/dev/null)
assets=$(echo "$html" | grep -oE '/assets/[^"]+\.js' | sort -u)
found_abs=0; got_bundle=0
for a in $assets; do
  body=$(curl -fsS "$WEB$a" 2>/dev/null) || continue
  got_bundle=1
  # Herestring, not a pipe: `grep -qF` exits on first match and SIGPIPEs the
  # `echo` producer; under `pipefail` that makes the whole pipeline non-zero on
  # large bundles, so `&& found_abs=1` never runs and a healthy host looks missing.
  grep -qF "$API_HOST" <<<"$body" && found_abs=1
done
if [ "$found_abs" = "1" ]; then
  pass "bundle references the absolute API host ($API_HOST)"
elif [ "$got_bundle" = "1" ]; then
  bad "bundle does not reference $API_HOST — VITE_API_URL inlined empty? use || not ?? in src/lib/api/client.ts"
else
  bad "could not load any /assets/*.js bundle from $WEB (asset names changed? inspect manually)"
fi

echo
if [ "$fail" = "0" ]; then
  printf '\033[32mAll stack checks passed.\033[0m\n'
else
  printf '\033[31mStack checks FAILED.\033[0m\n'
fi
exit "$fail"
