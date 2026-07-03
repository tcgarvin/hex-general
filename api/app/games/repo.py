"""Persistence boundary for the turn lifecycle (roadmap Phase 5).

This is an **I/O boundary**: it is the only code in the games package that talks
to Supabase, and it validates every response before handing typed records back to
the service. A query that should have returned a row but did not raises
`RepoError` rather than silently coping — a missing row for a known game id is a
broken invariant, not a domain answer. The genuinely-optional lookups
(`get_game`, `get_player`, `get_active_player_for_user`) return ``None`` for "no
such game / not a player / no active game", which the service maps to a 404/403;
that is real domain optionality, not an error signal.

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
    invite_token: str


class PlayerRecord(BaseModel):
    """A players row: a Supabase Auth user bound to one game slot.

    ``bot_policy`` names the AI policy driving this slot for a "vs bot" game
    (AI_SPEC §9), resolved through `app.ai.arena`'s registry; ``None`` means a
    human player. A bot row's ``user_id`` is a generated UUID matching no auth
    user, so the slot can never be claimed or driven from outside.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    game_id: str
    slot: str
    user_id: str
    bot_policy: str | None = None


class UserGameRecord(BaseModel):
    """A game a user holds a slot in, for their history (account screen).

    Carries the slot the user holds plus enough of the game to derive an outcome
    (``state`` for the victory check, ``turn`` from it). ``invite_token`` is
    deliberately excluded — history must not leak another game's open-slot secret.
    """

    model_config = ConfigDict(frozen=True)

    game_id: str
    scenario: str
    status: str
    state: GameState
    created_at: str
    slot: str


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

    def create_game(
        self, scenario: str, state: GameState, invite_token: str
    ) -> GameRecord: ...

    def get_game(self, game_id: str) -> GameRecord | None: ...

    def set_game_state(self, game_id: str, state: GameState, status: str) -> None: ...

    def create_player(
        self, game_id: str, slot: str, user_id: str, bot_policy: str | None = None
    ) -> PlayerRecord: ...

    def list_players(self, game_id: str) -> list[PlayerRecord]: ...

    def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None: ...

    def get_active_player_for_user(self, user_id: str) -> PlayerRecord | None: ...

    def list_user_games(self, user_id: str) -> list[UserGameRecord]: ...

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


def _game_record(row: dict[str, Any]) -> GameRecord:
    """Build a `GameRecord` from a games row, parsing the JSONB state blob."""
    return GameRecord(
        id=row["id"],
        scenario=row["scenario"],
        status=row["status"],
        state=GameState.model_validate(row["state"]),
        invite_token=row["invite_token"],
    )


def _player_record(row: dict[str, Any]) -> PlayerRecord:
    """Build a `PlayerRecord` from a players row."""
    return PlayerRecord(
        id=row["id"],
        game_id=row["game_id"],
        slot=row["slot"],
        user_id=row["user_id"],
        bot_policy=row["bot_policy"],
    )


class SupabaseGameRepo:
    """`GameRepo` backed by Supabase (service-role key; bypasses RLS)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def create_game(
        self, scenario: str, state: GameState, invite_token: str
    ) -> GameRecord:
        response = (
            self._client.table("games")
            .insert(
                {
                    "name": scenario,
                    "scenario": scenario,
                    "status": "lobby",
                    "state": state.model_dump(mode="json"),
                    "invite_token": invite_token,
                }
            )
            .execute()
        )
        row = _one_row(response, "create_game")
        return _game_record(row)

    def get_game(self, game_id: str) -> GameRecord | None:
        response = (
            self._client.table("games")
            .select("id, scenario, status, state, invite_token")
            .eq("id", game_id)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_game")
        return _game_record(row)

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

    def create_player(
        self, game_id: str, slot: str, user_id: str, bot_policy: str | None = None
    ) -> PlayerRecord:
        response = (
            self._client.table("players")
            .insert(
                {
                    "game_id": game_id,
                    "slot": slot,
                    "user_id": user_id,
                    "bot_policy": bot_policy,
                }
            )
            .execute()
        )
        return _player_record(_one_row(response, "create_player"))

    def list_players(self, game_id: str) -> list[PlayerRecord]:
        response = (
            self._client.table("players")
            .select("id, game_id, slot, user_id, bot_policy")
            .eq("game_id", game_id)
            .execute()
        )
        return [_player_record(r) for r in _rows(response)]

    def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None:
        response = (
            self._client.table("players")
            .select("id, game_id, slot, user_id, bot_policy")
            .eq("game_id", game_id)
            .eq("user_id", user_id)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        return _player_record(_one_row(response, "get_player"))

    def get_active_player_for_user(self, user_id: str) -> PlayerRecord | None:
        """Return the user's single un-finished player row (the dispatcher target).

        Joins ``games`` so a player row from a finished game does not count: a user
        is "active" only while they hold a slot in a lobby/active game.
        """
        response = (
            self._client.table("players")
            .select("id, game_id, slot, user_id, bot_policy, games!inner(status)")
            .eq("user_id", user_id)
            .neq("games.status", "finished")
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_active_player_for_user")
        return _player_record(row)

    def list_user_games(self, user_id: str) -> list[UserGameRecord]:
        """Return every game the user holds a slot in, for their history screen."""
        response = (
            self._client.table("players")
            .select("slot, games!inner(id, scenario, status, state, created_at)")
            .eq("user_id", user_id)
            .execute()
        )
        records: list[UserGameRecord] = []
        for row in _rows(response):
            game = cast(dict[str, Any], row["games"])
            records.append(
                UserGameRecord(
                    game_id=game["id"],
                    scenario=game["scenario"],
                    status=game["status"],
                    state=GameState.model_validate(game["state"]),
                    created_at=game["created_at"],
                    slot=row["slot"],
                )
            )
        return records

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
