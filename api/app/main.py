import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from app import __version__
from app.accounts import list_dev_profiles
from app.config import get_settings
from app.db import get_supabase_client
from app.games.auth import AuthIdentity, is_admin, is_dev
from app.games.deps import real_identity
from app.games.schemas import DevProfile, MeView
from app.games.world_routes import clock_router
from app.games.world_routes import dev_router as dev_worlds_router
from app.games.world_routes import router as worlds_router
from app.stats import count_active_users, count_games

app = FastAPI(title="Hex General API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=get_settings().cors_allow_origin_regex,
    # DELETE for the admin screen's world teardown (DELETE /dev/worlds/{id}).
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(worlds_router)
app.include_router(dev_worlds_router)
app.include_router(clock_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for Cloud Run and uptime checks. No external dependencies."""
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    """Return the running API version and the commit its image was built from.

    ``commit`` is the ``GIT_COMMIT`` the deploy bakes into the image (the pushed
    commit's SHA), so a smoke test can tell which build is live; ``"local"``
    means a process not started from a CI-built image.
    """
    return {"version": __version__, "commit": os.environ.get("GIT_COMMIT") or "local"}


@app.get("/me")
def me(identity: AuthIdentity = Depends(real_identity)) -> MeView:
    """Return the authenticated caller's own identity, dev role and admin role.

    Reports the *real* account (dev impersonation is not applied), so the frontend
    dev switcher and admin link stay visible for a dev even while they act as
    another user.
    """
    return MeView(
        user_id=identity.user_id,
        is_dev=is_dev(identity.user_id),
        is_admin=is_admin(identity.user_id),
    )


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
