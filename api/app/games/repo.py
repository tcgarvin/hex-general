"""Persistence boundary for the turn lifecycle (roadmap Phase 5).

This is an **I/O boundary**: it is the only code in the games package that talks
to Supabase, and it validates every response before handing typed records back to
the service. A query that should have returned a row but did not raises
`RepoError` rather than silently coping — a missing row for a known game id is a
broken invariant, not a domain answer. The two genuinely-optional lookups
(`get_game`, `get_player_by_token`) return ``None`` for "no such game / token",
which the service maps to a 404/401; that is real domain optionality, not an
error signal.

The service depends on the `GameRepo` *protocol*, not the Supabase class, so the
lifecycle logic is unit-testable against an in-memory fake.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict
from supabase import Client

from app.engine.models import GameState
from app.engine.victory import VictoryStatus


class RepoError(RuntimeError):
    """A persistence invariant was violated (e.g. an expected row was missing)."""


class GameRecord(BaseModel):
    """A games row with its canonical state parsed back into a `GameState`."""

    model_config = ConfigDict(frozen=True)

    id: str
    scenario: str
    status: str
    state: GameState


class PlayerRecord(BaseModel):
    """A players row: an opaque token bound to one game slot."""

    model_config = ConfigDict(frozen=True)

    id: str
    game_id: str
    slot: str
    token: str


class OrdersRecord(BaseModel):
    """A turn_orders row: one player's locked plan for one turn (raw JSON)."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    turn: int
    orders: dict[str, Any]


class TurnRecord(BaseModel):
    """A resolved turns row: the replay, resulting state, and victory snapshot."""

    model_config = ConfigDict(frozen=True)

    turn: int
    events: list[dict[str, Any]]
    state_after: GameState
    victory: VictoryStatus


class GameRepo(Protocol):
    """Persistence operations the lifecycle service depends on."""

    def create_game(self, scenario: str, state: GameState) -> GameRecord: ...

    def get_game(self, game_id: str) -> GameRecord | None: ...

    def set_game_state(self, game_id: str, state: GameState, status: str) -> None: ...

    def create_player(self, game_id: str, slot: str, token: str) -> PlayerRecord: ...

    def list_players(self, game_id: str) -> list[PlayerRecord]: ...

    def get_player_by_token(self, token: str) -> PlayerRecord | None: ...

    def record_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None: ...

    def list_turn_orders(self, game_id: str, turn: int) -> list[OrdersRecord]: ...

    def record_turn(
        self,
        game_id: str,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None: ...

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None: ...


def _rows(response: Any) -> list[dict[str, Any]]:
    """Cast a Supabase response's loosely-typed ``data`` to a list of row dicts.

    The Supabase client types ``data`` as a recursive JSON union; at this I/O
    boundary every row is a table record (a dict), so the cast is sound.
    """
    return cast(list[dict[str, Any]], response.data)


def _one_row(response: Any, context: str) -> dict[str, Any]:
    """Return the single row from a write that must have produced exactly one."""
    rows = _rows(response)
    if len(rows) != 1:
        raise RepoError(f"{context}: expected exactly one row, got {len(rows)}")
    return rows[0]


class SupabaseGameRepo:
    """`GameRepo` backed by Supabase (service-role key; bypasses RLS)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def create_game(self, scenario: str, state: GameState) -> GameRecord:
        response = (
            self._client.table("games")
            .insert(
                {
                    "name": scenario,
                    "scenario": scenario,
                    "status": "lobby",
                    "state": state.model_dump(mode="json"),
                }
            )
            .execute()
        )
        row = _one_row(response, "create_game")
        return GameRecord(
            id=row["id"],
            scenario=row["scenario"],
            status=row["status"],
            state=GameState.model_validate(row["state"]),
        )

    def get_game(self, game_id: str) -> GameRecord | None:
        response = (
            self._client.table("games")
            .select("id, scenario, status, state")
            .eq("id", game_id)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_game")
        return GameRecord(
            id=row["id"],
            scenario=row["scenario"],
            status=row["status"],
            state=GameState.model_validate(row["state"]),
        )

    def set_game_state(self, game_id: str, state: GameState, status: str) -> None:
        response = (
            self._client.table("games")
            .update(
                {
                    "state": state.model_dump(mode="json"),
                    "status": status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", game_id)
            .execute()
        )
        _one_row(response, "set_game_state")

    def create_player(self, game_id: str, slot: str, token: str) -> PlayerRecord:
        response = (
            self._client.table("players")
            .insert({"game_id": game_id, "slot": slot, "token": token})
            .execute()
        )
        row = _one_row(response, "create_player")
        return PlayerRecord(
            id=row["id"], game_id=row["game_id"], slot=row["slot"], token=row["token"]
        )

    def list_players(self, game_id: str) -> list[PlayerRecord]:
        response = (
            self._client.table("players")
            .select("id, game_id, slot, token")
            .eq("game_id", game_id)
            .execute()
        )
        return [
            PlayerRecord(
                id=r["id"], game_id=r["game_id"], slot=r["slot"], token=r["token"]
            )
            for r in _rows(response)
        ]

    def get_player_by_token(self, token: str) -> PlayerRecord | None:
        response = (
            self._client.table("players")
            .select("id, game_id, slot, token")
            .eq("token", token)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_player_by_token")
        return PlayerRecord(
            id=row["id"], game_id=row["game_id"], slot=row["slot"], token=row["token"]
        )

    def record_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        response = (
            self._client.table("turn_orders")
            .insert(
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "turn": turn,
                    "orders": orders,
                    "locked": True,
                }
            )
            .execute()
        )
        _one_row(response, "record_orders")

    def list_turn_orders(self, game_id: str, turn: int) -> list[OrdersRecord]:
        response = (
            self._client.table("turn_orders")
            .select("player_id, turn, orders")
            .eq("game_id", game_id)
            .eq("turn", turn)
            .execute()
        )
        return [
            OrdersRecord(player_id=r["player_id"], turn=r["turn"], orders=r["orders"])
            for r in _rows(response)
        ]

    def record_turn(
        self,
        game_id: str,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None:
        response = (
            self._client.table("turns")
            .insert(
                {
                    "game_id": game_id,
                    "turn": turn,
                    "events": events,
                    "state_after": state_after.model_dump(mode="json"),
                    "victory": victory.model_dump(mode="json"),
                }
            )
            .execute()
        )
        _one_row(response, "record_turn")

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None:
        response = (
            self._client.table("turns")
            .select("turn, events, state_after, victory")
            .eq("game_id", game_id)
            .eq("turn", turn)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_turn")
        return TurnRecord(
            turn=row["turn"],
            events=row["events"],
            state_after=GameState.model_validate(row["state_after"]),
            victory=VictoryStatus.model_validate(row["victory"]),
        )
