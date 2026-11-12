"""FastAPI dependencies shared by the game routes (`app.games.world_routes`, `/me`).

Auth model: every authenticated request carries a Supabase
``Authorization: Bearer <jwt>``; `real_identity` verifies it at the boundary (401
on failure). A dev (allowlisted in config) may additionally send
``X-Dev-Profile: <user_id>`` to impersonate another user for solo playtesting —
honored only for devs (403 otherwise), the same on local and deployed stacks.
Per-game endpoints then require that the user holds a slot in *that* game
(`require_player`). World management (the admin screen) requires an admin
(`require_admin`): a dev, or an account on the separate admin allowlist, which
grants no impersonation.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Path
from supabase import Client

from app.db import get_supabase_client
from app.games.auth import AuthError, AuthIdentity, is_admin, is_dev, verify_jwt
from app.games.repo import GameRepo, PlayerRecord, SupabaseGameRepo
from app.games.service import GameService


def get_repo(client: Client = Depends(get_supabase_client)) -> GameRepo:
    """Build the persistence boundary backed by Supabase for one request."""
    return SupabaseGameRepo(client)


def get_service(repo: GameRepo = Depends(get_repo)) -> GameService:
    """Build a `GameService` over the request's repo."""
    return GameService(repo)


def real_identity(
    authorization: str | None = Header(default=None),
) -> AuthIdentity:
    """Resolve the *real* authenticated caller from the Supabase bearer JWT.

    401 if the token is missing/malformed/invalid. Ignores ``X-Dev-Profile`` — this
    is the underlying account, so ``GET /me`` can report a dev's own role even while
    that dev is impersonating someone else via `current_identity`.
    """
    if authorization is None:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="expected a Bearer token")
    try:
        return verify_jwt(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def current_identity(
    identity: AuthIdentity = Depends(real_identity),
    x_dev_profile: str | None = Header(default=None),
) -> AuthIdentity:
    """Resolve the *effective* caller, honoring dev impersonation.

    If ``X-Dev-Profile`` is present the verified caller must be a dev (403 otherwise);
    the effective identity then becomes the named user, so a dev can act as any
    seeded account.
    """
    if x_dev_profile is not None:
        if not is_dev(identity.user_id):
            raise HTTPException(
                status_code=403, detail="not authorized to impersonate users"
            )
        return AuthIdentity(user_id=x_dev_profile)
    return identity


def require_player(
    game_id: str = Path(...),
    identity: AuthIdentity = Depends(current_identity),
    repo: GameRepo = Depends(get_repo),
) -> PlayerRecord:
    """Resolve the caller's player in the path's game (403 if they are not in it)."""
    player = repo.get_player(game_id, identity.user_id)
    if player is None:
        raise HTTPException(status_code=403, detail="not a player in this game")
    return player


def require_admin(
    identity: AuthIdentity = Depends(real_identity),
    _effective: AuthIdentity = Depends(current_identity),
) -> AuthIdentity:
    """403 unless the *real* caller is an admin (a dev or on ``ADMIN_USER_IDS``).

    The role is checked on the real account, so a dev who is acting as another
    user through the switcher can still manage worlds. ``_effective`` is resolved
    only for its gate: an ``X-Dev-Profile`` from a caller who is not a dev is still
    a 403, so the admin allowlist never grants impersonation.
    """
    if not is_admin(identity.user_id):
        raise HTTPException(status_code=403, detail="admin-only endpoint")
    return identity
