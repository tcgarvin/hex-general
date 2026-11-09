"""HTTP request/response models for the turn lifecycle API (roadmap Phase 5).

These are the wire boundary: FastAPI validates incoming bodies against the request
models (so a malformed `orders` list is a 422 before any handler runs) and
serializes the response models out. Game-domain types (`GameState`, `Order`,
`VictoryStatus`) are reused directly so the API speaks the same vocabulary as the
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
from app.engine.victory import VictoryStatus

# Default scenario a new game is built from when the request omits one.
DEFAULT_SCENARIO = "twin_rivers"


# Default AI opponent for a "vs bot" game (AI_SPEC §9): the Tier-1 SearchPolicy.
DEFAULT_BOT_POLICY = "search"


class CreateGameRequest(BaseModel):
    """Body for ``POST /games``: which authored scenario to instantiate.

    ``vs_bot`` requests a game against the computer (AI_SPEC §9): the service
    fills the second slot with a bot player and the game starts immediately.
    ``bot_policy`` picks the AI (resolved via `app.ai.arena`'s registry) and is
    only consulted when ``vs_bot`` is true.
    """

    model_config = ConfigDict(extra="forbid")

    scenario: str = DEFAULT_SCENARIO
    vs_bot: bool = False
    bot_policy: str = DEFAULT_BOT_POLICY


class SubmitOrdersRequest(BaseModel):
    """Body for ``POST /games/{id}/orders``: a player's full plan for the turn.

    Both lists default to empty: a player may legitimately submit no orders (every
    unit holds) and no recruits. FastAPI parses ``orders`` into the discriminated
    `Order` union, so unknown order shapes are rejected as 422 at the wire.
    """

    model_config = ConfigDict(extra="forbid")

    orders: list[Order] = Field(default_factory=list)
    recruitments: list[Recruitment] = Field(default_factory=list)
    # World games only: start the next level of this building at your base, and
    # who to let go first if a shortage forces a disband this turn.
    build: Building | None = None
    disband: list[UnitId] = Field(default_factory=list)


class JoinGameRequest(BaseModel):
    """Body for ``POST /games/{id}/join``: the invite token from the share link."""

    model_config = ConfigDict(extra="forbid")

    invite_token: str


class PlayerCredentials(BaseModel):
    """Returned on create/join: which slot the caller now holds, and the share link.

    Identity is the Supabase JWT (not returned here); ``invite_token`` lets the
    holder share the open slot.
    """

    game_id: str
    slot: PlayerId
    status: str
    invite_token: str


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


class ActiveGame(BaseModel):
    """The game a user is currently in (the dispatcher's redirect target)."""

    game_id: str
    slot: PlayerId
    # A world opens the world view (/world/:id); a match the 2-player board.
    kind: Literal["match", "world"]


class WorldSummary(BaseModel):
    """A running world as the join screen lists it (``GET /worlds``)."""

    game_id: str
    turn: int
    slots: int
    players: int
    humans: int
    clock: Literal["manual", "twice_daily"]
    next_tick_at: datetime | None


class DispatchView(BaseModel):
    """Where login should send a user: their active game, or nothing (start screen).

    ``active_game`` is ``None`` with exactly one meaning — the user holds no slot in
    any un-finished game, so the frontend shows the create / paste-join screen.
    """

    active_game: ActiveGame | None


class GameView(BaseModel):
    """A player's fog-filtered view of a game (SPEC §9).

    ``state`` is redacted to the caller's perspective: enemy units on hexes the
    caller cannot see are removed (the map, settlement control, and banks are
    public in v0.1). ``victory`` is the authoritative global outcome. ``visible``
    is the set of hexes the caller's units currently illuminate (SPEC §9), so the
    frontend can dim everything outside it as fog of war; the map itself is public,
    so fog is a rendering hint, not a redaction.
    """

    game_id: str
    status: str
    turn: int
    your_slot: PlayerId
    slots_present: list[PlayerId]
    locked: dict[PlayerId, bool]
    state: GameState
    visible: list[Axial]
    victory: VictoryStatus
    # The invite token for the still-open slot, or None once the game is full.
    invite_token: str | None
    # Whether the opponent is an AI (AI_SPEC §9). The caller is always a human
    # (bots never call the API), so this describes the other slot: ``vs_bot`` is
    # true iff a bot holds it, and ``bot_policy`` names that AI (e.g. "search"),
    # or ``None`` for a human opponent / a slot no one has claimed yet.
    vs_bot: bool
    bot_policy: str | None


class SubmitResult(BaseModel):
    """Outcome of submitting orders: always locked; executed iff all players in."""

    locked: bool
    executed: bool
    turn: int
    status: str


class TurnReplay(BaseModel):
    """A resolved turn's replay: the event log, resulting state, victory snapshot."""

    turn: int
    events: list[Event]
    state_after: GameState
    victory: VictoryStatus


class GameHistoryEntry(BaseModel):
    """One row of a user's game history (account screen).

    ``outcome`` is from the caller's perspective: ``win``/``loss``/``draw`` once the
    game is decided, else ``in_progress`` (a lobby or active game). ``turn`` is the
    current/final turn number; ``created_at`` is the game's ISO creation timestamp.
    """

    game_id: str
    scenario: str
    status: str
    your_slot: PlayerId
    turn: int
    outcome: str
    created_at: str
    # The opponent AI, mirroring `GameView` (AI_SPEC §9): ``vs_bot`` is true iff a
    # bot holds the other slot and ``bot_policy`` names it, else ``None``.
    vs_bot: bool
    bot_policy: str | None


# --- World games (WORLD_SPEC §10.1) ----------------------------------------------


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

    ``submitted`` says whether the caller's orders for this turn are already in;
    the turn itself resolves on the world's clock, not when everyone is in.
    """

    game_id: str
    status: str
    turn: int
    your_slot: PlayerId
    map_id: str
    submitted: bool
    state: WorldState
    visible: list[Axial]
    clock: Literal["manual", "twice_daily"]
    # When the current turn resolves on a scheduled world; None on a manual clock.
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
