"""Dump the FastAPI OpenAPI schema to ``api/openapi.json``.

The frontend generates its TypeScript API types from this file (single source of
truth for ``GameView``/``Order``/``DispatchView``/… — see ``frontend`` ``gen:api``),
so it is committed and regenerated whenever the API's request/response models change.

Importing ``app.main`` constructs ``Settings``, which requires the Supabase env vars;
this only builds the schema (no network), so dummy values are fine:

    SUPABASE_URL=x SUPABASE_SERVICE_ROLE_KEY=x uv run python -m app.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

# Repo-relative: api/openapi.json (this module lives at api/app/export_openapi.py).
_OUTPUT = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    """Write the app's OpenAPI schema to ``api/openapi.json`` (pretty-printed)."""
    schema = app.openapi()
    _OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {_OUTPUT}")


if __name__ == "__main__":
    main()
