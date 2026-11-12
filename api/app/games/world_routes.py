"""HTTP routes for world games (WORLD_SPEC §10.1, W5).

Thin adapter over `GameService`: it parses requests, resolves the caller's
identity (`app.games.deps`), calls the service, and maps the service's typed
`GameError`s onto HTTP status codes. All game logic lives in the service.

- ``GET /worlds``: the running worlds, to pick one to join.
- ``GET /worlds/active``: the world the caller holds a slot in, if any (where
  login sends them).
- ``POST /worlds/{id}/join``: take the first open slot in a running world.
- ``GET /worlds/{id}``: the caller's fogged view, without the map.
- ``GET /worlds/{id}/map``: the static map in the compact grid encoding. It never
  changes, so the response is marked cacheable for a year.
- ``GET /worlds/{id}/turns/{turn}``: the caller's replay of a resolved turn.
- ``POST /worlds/{id}/orders``: the caller's plan for the current turn, stored
  (and revisable) until the tick resolves it.
- ``GET /dev/worlds``, ``POST /dev/worlds``, ``POST /dev/worlds/{id}/tick`` and
  ``DELETE /dev/worlds/{id}``: world management for the admin screen (every
  world with its seats, creation, a manual clock and teardown). They need an
  admin (`require_admin`: a dev, or an account on ``ADMIN_USER_IDS``); the
  ``/dev`` prefix is historical and kept so the scripts keep working.
- ``POST /internal/clock``: the turn clock. Cloud Scheduler calls it at 01:00 and
  13:00 New York time with a Google OIDC token (`app.games.scheduler_auth`); it
  resolves every scheduled world that is due. ``POST /dev/clock`` runs the same
  sweep for an admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.ai.policy import economy_orders_for
from app.config import get_settings
from app.engine.map_codec import CompactMap
from app.games.auth import AuthIdentity
from app.games.deps import (
    current_identity,
    get_service,
    require_admin,
    require_player,
)
from app.games.repo import PlayerRecord
from app.games.scheduler_auth import (
    ClockNotConfigured,
    SchedulerAuthError,
    verify_scheduler_token,
)
from app.games.schemas import (
    ActiveWorldView,
    AdminWorldList,
    ClockRunView,
    CreateWorldRequest,
    PlayerCredentials,
    SubmitOrdersRequest,
    SubmitResult,
    WorldCreated,
    WorldReplay,
    WorldSummary,
    WorldView,
)
from app.games.service import (
    GameAlreadyActive,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    ReplayNotFound,
    WorldTicksFailed,
)
from app.games.validation import OrderValidationError
from app.world.generate import WorldGenError

router = APIRouter(prefix="/worlds", tags=["worlds"])
dev_router = APIRouter(prefix="/dev/worlds", tags=["worlds"])
clock_router = APIRouter(tags=["worlds"])

_MAP_CACHE_CONTROL = "private, max-age=31536000, immutable"


@router.get("")
def list_worlds(
    _identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> list[WorldSummary]:
    """Every running world, newest first, so a player can pick one to join."""
    return service.list_worlds()


# Declared before ``/{game_id}`` so "active" is not taken for a game id.
@router.get("/active")
def get_active_world(
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> ActiveWorldView:
    """The world the caller holds a slot in, or none (the join screen)."""
    return service.get_active_world(identity.user_id)


@router.post("/{game_id}/join", status_code=201)
def join_world(
    game_id: str,
    identity: AuthIdentity = Depends(current_identity),
    service: GameService = Depends(get_service),
) -> PlayerCredentials:
    """Seat the caller in the world's first open slot."""
    try:
        return service.join_world(game_id, identity.user_id)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (GameAlreadyActive, GameFull) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{game_id}")
def get_world(
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> WorldView:
    """The caller's fogged view of their world, without the map."""
    return service.get_world_view(player)


@router.get("/{game_id}/map")
def get_world_map(
    response: Response,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> CompactMap:
    """The world's static map (compact grid encoding); safe to cache forever."""
    compact = service.get_world_map(player)
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
    except ReplayNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{game_id}/orders")
def submit_orders(
    body: SubmitOrdersRequest,
    player: PlayerRecord = Depends(require_player),
    service: GameService = Depends(get_service),
) -> SubmitResult:
    """Validate and store the caller's plan; it resolves at the world's next tick.

    409 if the turn is closed: the world is not active, its scheduled tick is
    due, or a tick resolved the turn while the plan was being stored.
    """
    try:
        return service.submit_orders(
            player,
            body.orders,
            body.recruitments,
            economy_orders_for(player.slot, body.build, body.disband),
        )
    except OrderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GameNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@dev_router.get("")
def list_admin_worlds(
    _admin: AuthIdentity = Depends(require_admin),
    service: GameService = Depends(get_service),
) -> AdminWorldList:
    """Every world whatever its status, with its size and seats (admin-only)."""
    return service.list_admin_worlds()


@dev_router.post("", status_code=201)
def create_world(
    body: CreateWorldRequest,
    _admin: AuthIdentity = Depends(require_admin),
    service: GameService = Depends(get_service),
) -> WorldCreated:
    """Generate a world and fill ``bots`` slots with world bots (admin-only)."""
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
    _admin: AuthIdentity = Depends(require_admin),
    service: GameService = Depends(get_service),
) -> SubmitResult:
    """Resolve the world's current turn now: the manual clock (admin-only)."""
    try:
        return service.tick_world(game_id)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GameNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@dev_router.delete("/{game_id}", status_code=204)
def delete_world(
    game_id: str,
    _admin: AuthIdentity = Depends(require_admin),
    service: GameService = Depends(get_service),
) -> Response:
    """Delete a world and its players, orders, turns and map (admin-only)."""
    try:
        service.delete_world(game_id)
    except GameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


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
    _admin: AuthIdentity = Depends(require_admin),
    service: GameService = Depends(get_service),
) -> ClockRunView:
    """Run the scheduler's sweep now (admin-only): resolves whatever is due."""
    return _run_clock(service)
