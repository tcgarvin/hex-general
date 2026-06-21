---
name: verify-stack
description: Smoke-test the deployed hex-general stack end to end (browser → Cloud Run API → Supabase). Use after pushing a deploy, or when asked "is it live", "did the deploy work", or "why isn't /stats working".
---

# verify-stack

Runs one pass of checks against the **live** stack and reports pass/fail per
check. Built to catch the failure classes that local checks (pytest, mypy,
`next build`) can't see because they only appear after deploy:

- the API 500ing because a new table lacks a `service_role` GRANT
- the frontend fetching a relative `/stats` because an unset `NEXT_PUBLIC_` var
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
`HEX_WEB_URL=https://hex-general.vercel.app`, `HEX_WEB_ORIGIN=$HEX_WEB_URL`.

## What it checks

1. `GET /health` → `{"status":"ok"}`
2. `GET /version` → has a version
3. `GET /stats` → valid JSON with an integer `active_users` (proves the
   Cloud Run → Supabase leg)
4. CORS: `/stats` echoes `Access-Control-Allow-Origin` for the web origin
5. CORS: a disallowed origin gets **no** ACAO header
6. The frontend `/stats` page returns 200
7. The frontend JS bundle fetches an **absolute** API URL, not a relative
   `fetch("/stats")`

## Interpreting failures

- **Check 3 fails (empty/500):** the API can't read the table. Most likely a
  missing grant — `GRANT ... ON <table> TO service_role;` in a new migration.
  Confirm with the Cloud Run logs:
  `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="hex-api" AND severity>=ERROR' --project=hex-general --freshness=10m`
- **Check 4/5 fail:** the CORS `allow_origin_regex` in `api/app/config.py`
  doesn't match the real web origin (or matches too much).
- **Check 7 fails (relative fetch):** `NEXT_PUBLIC_API_URL` inlined empty;
  ensure `src/lib/api.ts` uses `||` (not `??`) for the default. After a
  frontend fix, the Vercel CDN may serve the old bundle briefly.

When a check fails, fix the root cause and re-run; don't paper over it.
