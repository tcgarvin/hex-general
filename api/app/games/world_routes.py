"""HTTP routes for world games (WORLD_SPEC §10.1, W5).

Thin adapter over `GameService`'s world operations, like `app.games.routes`:

- ``POST /worlds/{id}/join``: take the first open slot in a running world.
- ``GET /worlds/{id}``: the caller's fogged view, without the map.
- ``GET /worlds/{id}/map``: the static map in the compact grid encoding. It never
  changes, so the response is marked cacheable for a year.
- ``GET /worlds/{id}/turns/{turn}``: the caller's replay of a resolved turn.
- ``POST /dev/worlds`` and ``POST /dev/worlds/{id}/tick``: dev-only world
  creation and a manual clock. The scheduled tick for live worlds comes with the
  Cloud Scheduler job.

Orders go through ``POST /games/{id}/orders``, as for a match; for a world they
are stored and resolve at the next tick.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from app.engine.map_codec import CompactMap
from app.games.auth import AuthIdentity, is_dev
from app.games.repo import PlayerRecord
from app.games.routes import current_identity, get_service, require_player
from app.games.schemas import (
    CreateWorldRequest,
    PlayerCredentials,
    SubmitResult,
    WorldCreated,
    WorldReplay,
    WorldView,
)
from app.games.service import (
    GameAlreadyActive,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    NotAWorld,
    ReplayNotFound,
)
from app.world.generate import WorldGenError

router = APIRouter(prefix="/worlds", tags=["worlds"])
dev_router = APIRouter(prefix="/dev/worlds", tags=["worlds"])

_MAP_CACHE_CONTROL = "private, max-age=31536000, immutable"


def require_dev(identity: AuthIdentity = Depends(current_identity)) -> AuthIdentity:
    """403 unless the caller is an allowlisted dev."""
    if not is_dev(identity.user_id):
        raise HTTPException(status_code=403, detail="dev-only endpoint")
    return identity


@router.post("/{game_id}/join", status_code=201)
def join_world(
    game_id: str,
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> PlayerCredentials:
    """Seat the caller in the world's first open slot."""
    try:
        return service.join_world(game_id, identity.user_id)
    except (GameNotFound, NotAWorld) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (GameAlreadyActive, GameFull) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{game_id}")
def get_world(
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> WorldView:
    """The caller's fogged view of their world, without the map."""
    try:
        return service.get_world_view(player)
    except NotAWorld as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{game_id}/map")
def get_world_map(
    response: Response,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> CompactMap:
    """The world's static map (compact grid encoding); safe to cache forever."""
    try:
        compact = service.get_world_map(player)
    except NotAWorld as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response.headers["Cache-Control"] = _MAP_CACHE_CONTROL
    return compact


@router.get("/{game_id}/turns/{turn}")
def get_world_replay(
    turn: int,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> WorldReplay:
    """The caller's replay of a resolved world turn, without the map."""
    try:
        return service.get_world_replay(player, turn)
    except (NotAWorld, ReplayNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@dev_router.post("", status_code=201)
def create_world(
    body: CreateWorldRequest,
    _dev: AuthIdentity = Depends(require_dev),
    service: GameService = Depends(get_service),
) -> WorldCreated:
    """Generate a world and fill ``bots`` slots with world bots (dev-only)."""
    try:
        return WorldCreated(
            game_id=service.create_world(body.slots, body.seed, body.bots)
        )
    except WorldGenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@dev_router.post("/{game_id}/tick")
def tick_world(
    game_id: str,
    _dev: AuthIdentity = Depends(require_dev),
    service: GameService = Depends(get_service),
) -> SubmitResult:
    """Resolve the world's current turn now: the dev manual clock."""
    try:
        return service.tick_world(game_id)
    except (GameNotFound, NotAWorld) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GameNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
