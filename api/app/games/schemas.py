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


class PlayerCredentials(BaseModel):
    """Returned on create/join: the token the client must keep to act as its slot."""

    game_id: str
    slot: PlayerId
    token: str
    status: str


class GameView(BaseModel):
    """A player's view of a game (full state; fog of war arrives in Phase 6)."""

    game_id: str
    status: str
    turn: int
    your_slot: PlayerId
    slots_present: list[PlayerId]
    locked: dict[PlayerId, bool]
    state: GameState
    victory: VictoryStatus


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
