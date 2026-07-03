"""HTTP routes for the turn lifecycle (roadmap Phase 5, accounts in Phase 7).

Thin adapter over `GameService`: it parses requests, resolves the caller's
identity from the Supabase JWT, calls the service, and maps the service's typed
`GameError`s onto HTTP status codes. All game logic lives in the service; this
module only translates between HTTP and the domain.

Auth model (Phase 7): every authenticated request carries a Supabase
``Authorization: Bearer <jwt>``; `current_identity` verifies it at the boundary
(401 on failure). A dev (allowlisted in config) may additionally send
``X-Dev-Profile: <user_id>`` to impersonate another user for solo playtesting —
honored only for devs (403 otherwise), the same on local and deployed stacks.
Per-game endpoints then require that the user holds a slot in *that* game.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from supabase import Client

from app.db import get_supabase_client
from app.games.auth import AuthError, AuthIdentity, is_dev, verify_jwt
from app.games.repo import GameRepo, PlayerRecord, SupabaseGameRepo
from app.games.schemas import (
    CreateGameRequest,
    DispatchView,
    GameHistoryEntry,
    GameView,
    JoinGameRequest,
    PlayerCredentials,
    SubmitOrdersRequest,
    SubmitResult,
    TurnReplay,
)
from app.games.service import (
    AlreadySubmitted,
    GameAlreadyActive,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    InvalidInvite,
    ReplayNotFound,
    UnknownBotPolicy,
)
from app.games.validation import OrderValidationError

router = APIRouter(prefix="/games", tags=["games"])


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
    the effective identity then becomes the named user, so a dev can drive both sides
    of a game.
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


@router.post("", status_code=201)
def create_game(
    body: CreateGameRequest,
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> PlayerCredentials:
    """Create a game from a scenario and claim its first slot for the caller.

    With ``vs_bot`` the remaining slot is filled by an AI opponent and the game
    starts immediately (AI_SPEC §9).
    """
    try:
        return service.create_game(
            body.scenario,
            identity.user_id,
            vs_bot=body.vs_bot,
            bot_policy=body.bot_policy,
        )
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnknownBotPolicy as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GameAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/active")
def get_active_game(
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> DispatchView:
    """Dispatcher: the caller's active game (or none → start screen)."""
    return service.get_dispatch(identity.user_id)


@router.get("/history")
def get_history(
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> list[GameHistoryEntry]:
    """The caller's game history (newest first) for the account screen."""
    return service.get_history(identity.user_id)


@router.post("/{game_id}/join", status_code=201)
def join_game(
    game_id: str,
    body: JoinGameRequest,
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> PlayerCredentials:
    """Claim the next open slot in a game, gated by its invite token."""
    try:
        return service.join_game(game_id, identity.user_id, body.invite_token)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInvite as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GameAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GameFull as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{game_id}")
def get_game(
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> GameView:
    """Return the caller's view of the game."""
    return service.get_view(player)


@router.post("/{game_id}/orders")
def submit_orders(
    body: SubmitOrdersRequest,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> SubmitResult:
    """Validate and lock the caller's orders; resolve the turn if both are in."""
    try:
        return service.submit_orders(player, body.orders, body.recruitments)
    except OrderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GameNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AlreadySubmitted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{game_id}/resign")
def resign(
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> GameView:
    """Concede the caller's slot; ends the game if they were the last rival."""
    try:
        return service.resign(player)
    except GameNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{game_id}/turns/{turn}")
def get_replay(
    turn: int,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> TurnReplay:
    """Return the resolved replay (event log + state + victory) for a turn."""
    try:
        return service.get_replay(player, turn)
    except ReplayNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
