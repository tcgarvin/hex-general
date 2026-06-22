"""HTTP request/response models for the turn lifecycle API (roadmap Phase 5).

These are the wire boundary: FastAPI validates incoming bodies against the request
models (so a malformed `orders` list is a 422 before any handler runs) and
serializes the response models out. Game-domain types (`GameState`, `Order`,
`VictoryStatus`) are reused directly so the API speaks the same vocabulary as the
engine.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.engine.models import GameState, Order, PlayerId, Recruitment
from app.engine.victory import VictoryStatus

# Default scenario a new game is built from when the request omits one.
DEFAULT_SCENARIO = "twin_rivers"


class CreateGameRequest(BaseModel):
    """Body for ``POST /games``: which authored scenario to instantiate."""

    model_config = ConfigDict(extra="forbid")

    scenario: str = DEFAULT_SCENARIO


class SubmitOrdersRequest(BaseModel):
    """Body for ``POST /games/{id}/orders``: a player's full plan for the turn.

    Both lists default to empty: a player may legitimately submit no orders (every
    unit holds) and no recruits. FastAPI parses ``orders`` into the discriminated
    `Order` union, so unknown order shapes are rejected as 422 at the wire.
    """

    model_config = ConfigDict(extra="forbid")

    orders: list[Order] = Field(default_factory=list)
    recruitments: list[Recruitment] = Field(default_factory=list)


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


class ActiveGame(BaseModel):
    """The game a user is currently in (the dispatcher's redirect target)."""

    game_id: str
    slot: PlayerId


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
    public in v0.1). ``victory`` is the authoritative global outcome.
    """

    game_id: str
    status: str
    turn: int
    your_slot: PlayerId
    slots_present: list[PlayerId]
    locked: dict[PlayerId, bool]
    state: GameState
    victory: VictoryStatus
    # The invite token for the still-open slot, or None once the game is full.
    invite_token: str | None


class SubmitResult(BaseModel):
    """Outcome of submitting orders: always locked; executed iff all players in."""

    locked: bool
    executed: bool
    turn: int
    status: str


class TurnReplay(BaseModel):
    """A resolved turn's replay: the event log, resulting state, victory snapshot."""

    turn: int
    events: list[dict[str, Any]]
    state_after: GameState
    victory: VictoryStatus
