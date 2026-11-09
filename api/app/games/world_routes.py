"""HTTP routes for world games (WORLD_SPEC §10.1, W5).

Thin adapter over `GameService`'s world operations, like `app.games.routes`:

- ``POST /worlds/{id}/join``: take the first open slot in a running world.
- ``GET /worlds/{id}``: the caller's fogged view, without the map.
- ``GET /worlds/{id}/map``: the static map in the compact grid encoding. It never
  changes, so the response is marked cacheable for a year.
- ``GET /worlds/{id}/turns/{turn}``: the caller's replay of a resolved turn.
- ``POST /dev/worlds`` and ``POST /dev/worlds/{id}/tick``: dev-only world
  creation and a manual clock.
- ``POST /internal/clock``: the turn clock. Cloud Scheduler calls it at 01:00 and
  13:00 New York time with a Google OIDC token (`app.games.scheduler_auth`); it
  resolves every scheduled world that is due. ``POST /dev/clock`` runs the same
  sweep for a dev.

Orders go through ``POST /games/{id}/orders``, as for a match; for a world they
are stored and resolve at the next tick.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.engine.map_codec import CompactMap
from app.config import get_settings
from app.games.auth import AuthIdentity, is_dev
from app.games.repo import PlayerRecord
from app.games.routes import current_identity, get_service, require_player
from app.games.scheduler_auth import (
    ClockNotConfigured,
    SchedulerAuthError,
    verify_scheduler_token,
)
from app.games.schemas import (
    ClockRunView,
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
    WorldTicksFailed,
)
from app.world.generate import WorldGenError

router = APIRouter(prefix="/worlds", tags=["worlds"])
dev_router = APIRouter(prefix="/dev/worlds", tags=["worlds"])
clock_router = APIRouter(tags=["worlds"])

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
            game_id=service.create_world(
                body.slots, body.seed, body.bots, clock=body.clock
            )
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


def require_scheduler(authorization: str | None = Header(default=None)) -> None:
    """Admit only Cloud Scheduler's OIDC token (503 if the clock isn't configured)."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    settings = get_settings()
    try:
        verify_scheduler_token(
            authorization.removeprefix("Bearer "),
            settings.clock_audience,
            settings.clock_invoker_email,
        )
    except ClockNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SchedulerAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _run_clock(service: GameService) -> ClockRunView:
    try:
        run = service.tick_due_worlds()
    except WorldTicksFailed as exc:
        # 500 so the scheduler retries; the worlds that did resolve are no
        # longer due, so the retry only re-tries the failures.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ClockRunView(ticked=run.ticked, skipped=run.skipped)


@clock_router.post("/internal/clock")
def scheduled_clock(
    _scheduler: None = Depends(require_scheduler),
    service: GameService = Depends(get_service),
) -> ClockRunView:
    """Resolve every scheduled world whose turn is due (Cloud Scheduler only)."""
    return _run_clock(service)


@clock_router.post("/dev/clock")
def dev_clock(
    _dev: AuthIdentity = Depends(require_dev),
    service: GameService = Depends(get_service),
) -> ClockRunView:
    """Run the scheduler's sweep now (dev-only): resolves whatever is due."""
    return _run_clock(service)
