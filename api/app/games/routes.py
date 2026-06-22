"""HTTP routes for the turn lifecycle (roadmap Phase 5).

Thin adapter over `GameService`: it parses requests, resolves the caller's
identity from the ``X-Player-Token`` header, calls the service, and maps the
service's typed `GameError`s onto HTTP status codes. All game logic lives in the
service; this module only translates between HTTP and the domain.

Auth model: every per-game endpoint requires a token that resolves to a player in
*that* game. A token for a different game is treated as forbidden, so one game's
credentials can never read or act on another.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from supabase import Client

from app.db import get_supabase_client
from app.games.repo import GameRepo, PlayerRecord, SupabaseGameRepo
from app.games.schemas import (
    CreateGameRequest,
    GameView,
    PlayerCredentials,
    SubmitOrdersRequest,
    SubmitResult,
    TurnReplay,
)
from app.games.service import (
    AlreadySubmitted,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    ReplayNotFound,
)
from app.games.validation import OrderValidationError

router = APIRouter(prefix="/games", tags=["games"])


def get_repo(client: Client = Depends(get_supabase_client)) -> GameRepo:
    """Build the persistence boundary backed by Supabase for one request."""
    return SupabaseGameRepo(client)


def get_service(repo: GameRepo = Depends(get_repo)) -> GameService:
    """Build a `GameService` over the request's repo."""
    return GameService(repo)


def require_player(
    game_id: str = Path(...),
    x_player_token: str = Header(...),
    repo: GameRepo = Depends(get_repo),
) -> PlayerRecord:
    """Resolve the caller's player from the token and bind it to the path's game.

    401 if the token is unknown; 403 if it belongs to a different game.
    """
    player = repo.get_player_by_token(x_player_token)
    if player is None:
        raise HTTPException(status_code=401, detail="unknown player token")
    if player.game_id != game_id:
        raise HTTPException(status_code=403, detail="token is for a different game")
    return player


@router.post("", status_code=201)
def create_game(
    body: CreateGameRequest, service: GameService = Depends(get_service)
) -> PlayerCredentials:
    """Create a game from a scenario and return the creator's credentials."""
    try:
        return service.create_game(body.scenario)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{game_id}/join", status_code=201)
def join_game(
    game_id: str, service: GameService = Depends(get_service)
) -> PlayerCredentials:
    """Claim the next open slot in a game."""
    try:
        return service.join_game(game_id)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
