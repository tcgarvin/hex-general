"""In-memory `GameRepo` for testing the lifecycle service without Supabase.

Mirrors the Supabase repo's contract: typed records in, typed records out, the
two optional lookups return ``None`` for genuine absence, and the unique
constraints the real tables enforce (one slot per game, one submission per player
per turn) are reproduced so the service is exercised against the same invariants.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.engine.models import GameState
from app.engine.victory import VictoryStatus
from app.games.repo import GameRecord, OrdersRecord, PlayerRecord, RepoError, TurnRecord


class FakeGameRepo:
    """A dict-backed implementation of the `GameRepo` protocol."""

    def __init__(self) -> None:
        self._games: dict[str, GameRecord] = {}
        self._players: dict[str, PlayerRecord] = {}
        self._orders: dict[tuple[str, int, str], OrdersRecord] = {}
        self._turns: dict[tuple[str, int], TurnRecord] = {}

    def create_game(self, scenario: str, state: GameState) -> GameRecord:
        game = GameRecord(
            id=str(uuid.uuid4()), scenario=scenario, status="lobby", state=state
        )
        self._games[game.id] = game
        return game

    def get_game(self, game_id: str) -> GameRecord | None:
        return self._games.get(game_id)

    def set_game_state(self, game_id: str, state: GameState, status: str) -> None:
        game = self._games[game_id]
        self._games[game_id] = game.model_copy(
            update={"state": state, "status": status}
        )

    def create_player(self, game_id: str, slot: str, token: str) -> PlayerRecord:
        if any(p.game_id == game_id and p.slot == slot for p in self._players.values()):
            raise RepoError(f"slot {slot} already claimed in game {game_id}")
        player = PlayerRecord(
            id=str(uuid.uuid4()), game_id=game_id, slot=slot, token=token
        )
        self._players[player.id] = player
        return player

    def list_players(self, game_id: str) -> list[PlayerRecord]:
        return [p for p in self._players.values() if p.game_id == game_id]

    def get_player_by_token(self, token: str) -> PlayerRecord | None:
        for player in self._players.values():
            if player.token == token:
                return player
        return None

    def record_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        key = (game_id, turn, player_id)
        if key in self._orders:
            raise RepoError(f"orders already recorded for {key}")
        self._orders[key] = OrdersRecord(player_id=player_id, turn=turn, orders=orders)

    def list_turn_orders(self, game_id: str, turn: int) -> list[OrdersRecord]:
        return [
            record
            for (g, t, _), record in self._orders.items()
            if g == game_id and t == turn
        ]

    def record_turn(
        self,
        game_id: str,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None:
        key = (game_id, turn)
        if key in self._turns:
            raise RepoError(f"turn {turn} already recorded for game {game_id}")
        self._turns[key] = TurnRecord(
            turn=turn, events=events, state_after=state_after, victory=victory
        )

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None:
        return self._turns.get((game_id, turn))
