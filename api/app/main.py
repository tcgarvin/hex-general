from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from app import __version__
from app.accounts import list_dev_profiles
from app.config import get_settings
from app.db import get_supabase_client
from app.games.auth import AuthIdentity, is_dev
from app.games.routes import real_identity
from app.games.routes import router as games_router
from app.games.schemas import DevProfile, MeView
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


@app.get("/me")
def me(identity: AuthIdentity = Depends(real_identity)) -> MeView:
    """Return the authenticated caller's own identity and dev role.

    Reports the *real* account (dev impersonation is not applied), so the frontend
    dev switcher stays visible for a dev even while they act as another user.
    """
    return MeView(user_id=identity.user_id, is_dev=is_dev(identity.user_id))


@app.get("/dev/profiles")
def dev_profiles(
    identity: AuthIdentity = Depends(real_identity),
    client: Client = Depends(get_supabase_client),
) -> list[DevProfile]:
    """List the accounts the dev switcher can impersonate.

    Dev-only (403 otherwise). Returns the calling dev's own account plus every
    ``@hex.local`` test account, read server-side via the service-role admin API.
    """
    if not is_dev(identity.user_id):
        raise HTTPException(
            status_code=403, detail="not authorized to list dev profiles"
        )
    return list_dev_profiles(client, identity.user_id)


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
