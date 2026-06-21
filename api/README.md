# Hex General API

FastAPI service that hosts the Hex Wargame deterministic execution engine. Deployed to Cloud Run (GCP project `hex-general`). Talks to Supabase server-side using a service-role key sourced from GCP Secret Manager.

Managed with [uv](https://docs.astral.sh/uv/).

## Local development

```bash
cd api
uv sync                      # creates .venv from uv.lock (Python pinned in .python-version)

# /health and /version need no config; endpoints using Supabase do:
export SUPABASE_URL="https://amhkpsrvjlthmmemwssc.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="..."   # never commit; never expose to the browser

uv run uvicorn app.main:app --reload --port 8080
```

## Tests / checks

```bash
uv run pytest
uv run black --check .
uv run ruff check .
uv run mypy .
```

## Container

```bash
docker build -t hex-api .
docker run -p 8080:8080 -e PORT=8080 hex-api
```

Deployed automatically by `.github/workflows/api.yml` on pushes to `main` that touch `api/**`.
