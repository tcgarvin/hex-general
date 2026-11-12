"""In-memory `GameRepo` for testing the lifecycle service without Supabase.

Mirrors the Supabase repo's contract: typed records in, typed records out, the
optional lookups return ``None`` for genuine absence, states round-trip through
the same map-apart storage encoding, and the unique constraints the real tables
enforce (one slot per game, one user per game, one turns row per turn) are
reproduced so the service is exercised against the same invariants.
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
    AdminWorldRow,
    Clock,
    GameRecord,
    Keep,
    OrdersRecord,
    PlayerRecord,
    RepoError,
    StaleGame,
    TurnRecord,
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
        self._map_params: dict[str, dict[str, Any] | None] = {}

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
        self._map_params[map_id] = params
        return map_id

    def get_map(self, map_id: str) -> GameMap:
        return self._maps[map_id]

    def get_map_slots(self, map_id: str) -> list[Axial]:
        return list(self._map_slots[map_id])

    def _stored(self, state: GameState, map_id: str) -> GameState:
        """``state`` as it reads back from storage (its stored map rejoined)."""
        return join_state(state_blob(state), self._maps[map_id])

    def create_game(
        self,
        scenario: str,
        state: GameState,
        *,
        map_id: str,
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
            map_id=map_id,
        )
        self._games[game.id] = game
        # Monotonic per-creation timestamp so newest-first listing is deterministic.
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

    def list_worlds(self) -> list[WorldRow]:
        worlds = [g for g in self._games.values() if g.status == "active"]
        return [
            WorldRow(
                id=g.id,
                map_id=g.map_id,
                turn=g.state.turn,
                clock=g.clock,
                next_tick_at=g.next_tick_at,
            )
            for g in sorted(worlds, key=lambda g: self._created_at[g.id], reverse=True)
        ]

    def list_admin_worlds(self) -> list[AdminWorldRow]:
        rows = []
        for g in sorted(
            self._games.values(), key=lambda g: self._created_at[g.id], reverse=True
        ):
            game_map = self._maps[g.map_id]
            params = self._map_params[g.map_id]
            players = self.list_players(g.id)
            bots = sum(1 for p in players if p.bot_policy is not None)
            rows.append(
                AdminWorldRow(
                    id=g.id,
                    status=g.status,
                    turn=g.state.turn,
                    clock=g.clock,
                    next_tick_at=g.next_tick_at,
                    created_at=self._created_at[g.id],
                    width=game_map.width,
                    height=game_map.height,
                    slots=len(self._map_slots[g.map_id]),
                    seed=None if params is None else params["seed"],
                    humans=len(players) - bots,
                    bots=bots,
                )
            )
        return rows

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

    def delete_world(self, game_id: str, map_id: str) -> None:
        del self._games[game_id]
        self._created_at.pop(game_id)
        player_ids = {p.id for p in self._players.values() if p.game_id == game_id}
        for player_id in player_ids:
            del self._players[player_id]
        self._orders = {k: v for k, v in self._orders.items() if k[0] != game_id}
        self._turns = {k: v for k, v in self._turns.items() if k[0] != game_id}
        if not any(g.map_id == map_id for g in self._games.values()):
            del self._maps[map_id]
            del self._map_slots[map_id]
            del self._map_params[map_id]
