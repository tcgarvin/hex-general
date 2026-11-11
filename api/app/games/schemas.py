"""HTTP request/response models for the world game API (WORLD_SPEC §10.1).

These are the wire boundary: FastAPI validates incoming bodies against the request
models (so a malformed `orders` list is a 422 before any handler runs) and
serializes the response models out. Game-domain types (`Order`, `Unit`,
`Settlement`, …) are reused directly so the API speaks the same vocabulary as the
engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.economy_rules import Building
from app.engine.events import Event
from app.engine.hex_coords import Axial
from app.engine.models import (
    GameConfig,
    GameState,
    Order,
    Player,
    PlayerId,
    Recruitment,
    Settlement,
    StandingTravel,
    Unit,
    UnitId,
)


class SubmitOrdersRequest(BaseModel):
    """Body for ``POST /worlds/{id}/orders``: a player's full plan for the turn.

    Every field defaults to empty: a player may legitimately submit no orders
    (every unit holds or keeps travelling), no recruits and no base orders.
    FastAPI parses ``orders`` into the discriminated `Order` union, so unknown
    order shapes are rejected as 422 at the wire.
    """

    model_config = ConfigDict(extra="forbid")

    orders: list[Order] = Field(default_factory=list)
    recruitments: list[Recruitment] = Field(default_factory=list)
    # Base orders: start the next level of this building at your base, and who
    # to let go first if a shortage forces a disband this turn.
    build: Building | None = None
    disband: list[UnitId] = Field(default_factory=list)


class PlayerCredentials(BaseModel):
    """Returned on joining a world: which slot the caller now holds.

    Identity is the Supabase JWT (not returned here).
    """

    game_id: str
    slot: PlayerId
    status: str


class MeView(BaseModel):
    """The authenticated caller's own identity and dev role.

    Backs the frontend dev profile switcher: ``is_dev`` decides whether the switcher
    is shown, and reflects the *real* account (impersonation is not applied).
    """

    user_id: str
    is_dev: bool


class DevProfile(BaseModel):
    """A selectable account in the dev profile switcher.

    The switcher lists the calling dev's own account plus the ``@hex.local`` test
    accounts, so a dev picks who to impersonate from a menu rather than pasting a
    raw user id. ``is_self`` marks the dev's real account.
    """

    user_id: str
    email: str
    is_self: bool


class ActiveWorld(BaseModel):
    """The world a user is currently in (where login sends them)."""

    game_id: str
    slot: PlayerId


class WorldSummary(BaseModel):
    """A running world as the join screen lists it (``GET /worlds``)."""

    game_id: str
    turn: int
    slots: int
    players: int
    humans: int
    clock: Literal["manual", "twice_daily"]
    next_tick_at: datetime | None


class ActiveWorldView(BaseModel):
    """Where login should send a user: their active world, or nothing.

    ``active_world`` is ``None`` with exactly one meaning — the user holds no slot
    in any un-finished world, so the frontend shows the world list to join one.
    """

    active_world: ActiveWorld | None


class SubmitResult(BaseModel):
    """Outcome of submitting orders or of a tick.

    A submission is stored and locked but not executed (it resolves at the
    tick); a tick reports ``executed`` and the new turn and status.
    """

    locked: bool
    executed: bool
    turn: int
    status: str


class WorldState(BaseModel):
    """A world `GameState` without its static map.

    A world map is ~10^5 hexes, so the client fetches it once
    (``GET /worlds/{id}/map``) and every per-turn payload carries this instead.
    The fields mirror `GameState` minus ``map``; a test keeps them in step.
    """

    turn: int
    settlements: list[Settlement]
    units: list[Unit]
    players: list[Player]
    resigned: list[PlayerId]
    standing_orders: list[StandingTravel]
    config: GameConfig

    @classmethod
    def of(cls, state: GameState) -> WorldState:
        """``state`` without its map (sharing, not copying, its parts)."""
        return cls(**{name: getattr(state, name) for name in cls.model_fields})


class CreateWorldRequest(BaseModel):
    """Body for ``POST /dev/worlds``: the world to generate and how many bots."""

    model_config = ConfigDict(extra="forbid")

    slots: int = Field(ge=1, le=400)
    seed: int = 0
    bots: int = Field(ge=0)
    # "manual" moves only when ticked (dev); "twice_daily" resolves at 01:00 and
    # 13:00 New York time on the scheduler.
    clock: Literal["manual", "twice_daily"] = "manual"


class WorldCreated(BaseModel):
    """Returned by ``POST /dev/worlds``."""

    game_id: str


class WorldView(BaseModel):
    """A player's fog-filtered view of a world (the map comes separately).

    ``submission`` is the caller's plan for this turn as last submitted, which they
    may revise until the turn resolves; ``None`` means they have submitted nothing
    this turn. The turn itself resolves on the world's clock, not when everyone
    is in.
    """

    game_id: str
    status: str
    turn: int
    your_slot: PlayerId
    map_id: str
    submission: SubmitOrdersRequest | None
    state: WorldState
    visible: list[Axial]
    clock: Literal["manual", "twice_daily"]
    # When the current turn resolves: the scheduled clock's next tick, or the time
    # a local tick loop announced on a manual world; None when nothing has said.
    next_tick_at: datetime | None


class ClockRunView(BaseModel):
    """Returned by the clock endpoint: worlds resolved, and those already done."""

    ticked: list[str]
    skipped: list[str]


class WorldReplay(BaseModel):
    """A resolved world turn for one viewer: witnessed events and the fogged state."""

    turn: int
    events: list[Event]
    state_after: WorldState
