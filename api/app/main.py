from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from app import __version__
from app.config import get_settings
from app.db import get_supabase_client
from app.games.routes import router as games_router
from app.stats import count_active_users, count_games

app = FastAPI(title="Hex General API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=get_settings().cors_allow_origin_regex,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(games_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for Cloud Run and uptime checks. No external dependencies."""
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    """Return the running API version."""
    return {"version": __version__}


@app.get("/stats")
def stats(client: Client = Depends(get_supabase_client)) -> dict[str, int]:
    """Return live game stats sourced from Supabase.

    Demonstrates the full browser -> Cloud Run -> Supabase chain: the counts are
    read server-side with the service-role key.
    """
    return {
        "active_users": count_active_users(client),
        "game_count": count_games(client),
    }
