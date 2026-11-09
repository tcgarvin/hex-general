"""In-memory `GameRepo` for testing the lifecycle service without Supabase.

Mirrors the Supabase repo's contract: typed records in, typed records out, the
two optional lookups return ``None`` for genuine absence, and the unique
constraints the real tables enforce (one slot per game, one submission per player
per turn) are reproduced so the service is exercised against the same invariants.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.engine.hex_coords import Axial
from app.engine.map_codec import decode_map, encode_map
from app.engine.models import GameMap, GameState
from app.engine.victory import VictoryStatus
from app.games.repo import (
    KEEP,
    Clock,
    GameRecord,
    Keep,
    OrdersRecord,
    PlayerRecord,
    RepoError,
    StaleGame,
    TurnRecord,
    UserGameRecord,
    WorldRow,
    join_state,
    state_blob,
)


class FakeGameRepo:
    """A dict-backed implementation of the `GameRepo` protocol."""

    def __init__(self) -> None:
        self._games: dict[str, GameRecord] = {}
        self._created_at: dict[str, str] = {}
        self._players: dict[str, PlayerRecord] = {}
        self._orders: dict[tuple[str, int, str], OrdersRecord] = {}
        self._turns: dict[tuple[str, int], TurnRecord] = {}
        self._maps: dict[str, GameMap] = {}
        self._map_slots: dict[str, list[Axial]] = {}

    def create_map(
        self,
        game_map: GameMap,
        params: dict[str, Any] | None,
        slots: list[Axial],
    ) -> str:
        map_id = str(uuid.uuid4())
        # Round-trip through the storage encoding, as the real table does.
        self._maps[map_id] = decode_map(encode_map(game_map))
        self._map_slots[map_id] = list(slots)
        return map_id

    def get_map(self, map_id: str) -> GameMap:
        return self._maps[map_id]

    def get_map_slots(self, map_id: str) -> list[Axial]:
        return list(self._map_slots[map_id])

    def _stored(self, state: GameState, map_id: str | None) -> GameState:
        """``state`` as it reads back from storage (map rejoined if stored apart)."""
        game_map = None if map_id is None else self._maps[map_id]
        return join_state(state_blob(state, map_id), game_map)

    def create_game(
        self,
        scenario: str,
        state: GameState,
        invite_token: str,
        *,
        map_id: str | None = None,
        clock: Clock = "manual",
        next_tick_at: datetime | None = None,
    ) -> GameRecord:
        game = GameRecord(
            clock=clock,
            next_tick_at=next_tick_at,
            id=str(uuid.uuid4()),
            scenario=scenario,
            status="lobby",
            state=self._stored(state, map_id),
            invite_token=invite_token,
            map_id=map_id,
        )
        self._games[game.id] = game
        # Monotonic per-creation timestamp so history ordering is deterministic.
        self._created_at[game.id] = f"2026-01-01T00:00:{len(self._created_at):02d}Z"
        return game

    def get_game(self, game_id: str) -> GameRecord | None:
        return self._games.get(game_id)

    def set_game_state(
        self,
        game: GameRecord,
        state: GameState,
        status: str,
        *,
        next_tick_at: datetime | None | Keep = KEEP,
    ) -> None:
        stored = self._games[game.id]
        if stored.revision != game.revision:
            raise StaleGame(f"game {game.id} changed since revision {game.revision}")
        update: dict[str, Any] = {
            "state": self._stored(state, game.map_id),
            "status": status,
            "revision": stored.revision + 1,
        }
        if not isinstance(next_tick_at, Keep):
            update["next_tick_at"] = next_tick_at
        self._games[game.id] = stored.model_copy(update=update)

    def is_world_game(self, game_id: str) -> bool:
        return self._games[game_id].map_id is not None

    def list_worlds(self) -> list[WorldRow]:
        worlds = [
            g for g in self._games.values() if g.is_world and g.status == "active"
        ]
        return [
            WorldRow(
                id=g.id,
                map_id=g.map_id or "",
                turn=g.state.turn,
                clock=g.clock,
                next_tick_at=g.next_tick_at,
            )
            for g in sorted(worlds, key=lambda g: self._created_at[g.id], reverse=True)
        ]

    def list_due_worlds(self, now: datetime) -> list[str]:
        due = [
            g
            for g in self._games.values()
            if g.clock == "twice_daily"
            and g.status == "active"
            and g.next_tick_at is not None
            and g.next_tick_at <= now
        ]
        return [g.id for g in sorted(due, key=lambda g: (g.next_tick_at, g.id))]

    def create_player(
        self, game_id: str, slot: str, user_id: str, bot_policy: str | None = None
    ) -> PlayerRecord:
        if any(p.game_id == game_id and p.slot == slot for p in self._players.values()):
            raise RepoError(f"slot {slot} already claimed in game {game_id}")
        if any(
            p.game_id == game_id and p.user_id == user_id
            for p in self._players.values()
        ):
            raise RepoError(f"user {user_id} already in game {game_id}")
        player = PlayerRecord(
            id=str(uuid.uuid4()),
            game_id=game_id,
            slot=slot,
            user_id=user_id,
            bot_policy=bot_policy,
        )
        self._players[player.id] = player
        return player

    def list_players(self, game_id: str) -> list[PlayerRecord]:
        return [p for p in self._players.values() if p.game_id == game_id]

    def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None:
        for player in self._players.values():
            if player.game_id == game_id and player.user_id == user_id:
                return player
        return None

    def get_active_player_for_user(self, user_id: str) -> PlayerRecord | None:
        for player in self._players.values():
            if player.user_id != user_id:
                continue
            if self._games[player.game_id].status != "finished":
                return player
        return None

    def list_user_games(self, user_id: str) -> list[UserGameRecord]:
        records: list[UserGameRecord] = []
        for player in self._players.values():
            if player.user_id != user_id:
                continue
            game = self._games[player.game_id]
            opponent_bot_policy = next(
                (
                    p.bot_policy
                    for p in self._players.values()
                    if p.game_id == game.id
                    and p.slot != player.slot
                    and p.bot_policy is not None
                ),
                None,
            )
            records.append(
                UserGameRecord(
                    game_id=game.id,
                    scenario=game.scenario,
                    status=game.status,
                    state=game.state,
                    created_at=self._created_at[game.id],
                    slot=player.slot,
                    bot_policy=opponent_bot_policy,
                )
            )
        return records

    def record_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        key = (game_id, turn, player_id)
        if key in self._orders:
            raise RepoError(f"orders already recorded for {key}")
        self._orders[key] = OrdersRecord(player_id=player_id, turn=turn, orders=orders)

    def replace_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        key = (game_id, turn, player_id)
        self._orders[key] = OrdersRecord(player_id=player_id, turn=turn, orders=orders)

    def list_turn_orders(self, game_id: str, turn: int) -> list[OrdersRecord]:
        return [
            record
            for (g, t, _), record in self._orders.items()
            if g == game_id and t == turn
        ]

    def record_turn(
        self,
        game: GameRecord,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None:
        key = (game.id, turn)
        if key in self._turns:
            raise RepoError(f"turn {turn} already recorded for game {game.id}")
        self._turns[key] = TurnRecord(
            turn=turn,
            events=events,
            state_after=self._stored(state_after, game.map_id),
            victory=victory,
        )

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None:
        return self._turns.get((game_id, turn))
