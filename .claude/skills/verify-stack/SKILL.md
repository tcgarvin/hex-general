---
name: verify-stack
description: Smoke-test the deployed hex-general stack end to end (browser → Cloud Run API → Supabase). Use after pushing a deploy, or when asked "is it live", "did the deploy work", or "why isn't /stats working".
---

# verify-stack

Runs one pass of checks against the **live** stack and reports pass/fail per
check. Built to catch the failure classes that local checks (pytest, mypy,
`vite build`) can't see because they only appear after deploy:

- the API 500ing because a new table lacks a `service_role` GRANT
- the frontend bundle missing the absolute API URL because an unset `VITE_` var
  inlined as `""` (`??` doesn't fall back on empty string — use `||`)

## How to run

```bash
bash .claude/skills/verify-stack/verify.sh
```

Exits non-zero if any check fails. Override targets when checking a preview
deployment or after URLs change:

```bash
HEX_API_URL=https://... HEX_WEB_URL=https://... bash .claude/skills/verify-stack/verify.sh
```

Defaults: `HEX_API_URL=https://hex-api-qfzj5tg56a-uc.a.run.app`,
`HEX_WEB_URL=https://hex-general.vercel.app`, `HEX_WEB_ORIGIN=$HEX_WEB_URL`,
`HEX_EXPECT_COMMIT` unset (set it to demand one exact deployed commit).

## What it checks

1. `GET /health` → `{"status":"ok"}`
2. `GET /version` → has a version
3. World build (only after the world cutover; the old API answers 404 and an
   old image has no `commit`, so these four fail until the new build is live):
   - `/version` `commit` is in local history and contains the last local commit
     that touched `api/` or `.github/workflows/api.yml` (the image is built from
     the head of the push that last deployed the API; a push that changes
     neither does not redeploy)
   - `GET /worlds` without a token → 401
   - `POST /internal/clock` without a token → 401
   - `POST /internal/clock` with a malformed bearer → 403, not 503. A missing
     token is refused before the clock config is read, so only a token (any
     junk; it is rejected) shows whether the clock env vars are set.
4. `GET /stats` → valid JSON with an integer `active_users` (proves the
   Cloud Run → Supabase leg)
5. CORS: `/stats` echoes `Access-Control-Allow-Origin` for the web origin
6. CORS: a disallowed origin gets **no** ACAO header
7. The frontend `/stats` page returns 200
8. The frontend `/assets/index-<hash>.js` bundle references the **absolute**
   API host (proves `VITE_API_URL` was inlined, not empty)

## Interpreting failures

- **Check 3 fails:** `commit` mismatch means the old revision is still
  serving (deploy unfinished or failed); 404s mean the pre-world API; a 503
  from `/internal/clock` means `CLOCK_AUDIENCE` / `CLOCK_INVOKER_EMAIL` are
  missing from the Cloud Run service (`api.yml` sets them on deploy).
- **Check 4 fails (empty/500):** the API can't read the table. Most likely a
  missing grant — `GRANT ... ON <table> TO service_role;` in a new migration.
  Confirm with the Cloud Run logs:
  `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="hex-api" AND severity>=ERROR' --project=hex-general --freshness=10m`
- **Check 5/6 fail:** the CORS `allow_origin_regex` in `api/app/config.py`
  doesn't match the real web origin (or matches too much).
- **Check 8 fails (no absolute host):** `VITE_API_URL` inlined empty; ensure
  `src/lib/api/client.ts` uses `||` (not `??`) for the default. After a
  frontend fix, the Vercel CDN may serve the old bundle briefly.

When a check fails, fix the root cause and re-run; don't paper over it.
