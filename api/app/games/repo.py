"""Persistence boundary for world games (WORLD_SPEC §10.1).

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

A world stores its static map once (WORLD_SPEC §10.1): every games row has a
``map_id`` and keeps ``games.state`` and ``turns.state_after`` *without* the map,
and the repo rejoins the stored map on load, so the service still sees whole
`GameState`s. Decoded maps are cached per process (`MapCache`), because a map row
never changes and decoding a world map takes about two seconds.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict
from supabase import Client

from app.engine.hex_coords import Axial
from app.engine.map_codec import CompactMap, decode_map, encode_map
from app.engine.models import GameMap, GameState
from app.engine.victory import VictoryStatus


class RepoError(RuntimeError):
    """A persistence invariant was violated (e.g. an expected row was missing)."""


class StaleGame(Exception):
    """A state write lost a race: the game changed since its record was read.

    Not a broken invariant but a normal outcome of concurrent writers (a join
    landing during a tick). The caller re-reads the game and tries again.
    """


class Keep(Enum):
    """Sentinel for `GameRepo.set_game_state`: leave ``next_tick_at`` as it is."""

    KEEP = "keep"


KEEP = Keep.KEEP
Clock = Literal["manual", "twice_daily"]


class WorldRow(BaseModel):
    """A world's games row without its state (for listing)."""

    model_config = ConfigDict(frozen=True)

    id: str
    map_id: str
    turn: int
    clock: Clock
    next_tick_at: datetime | None


class GameRecord(BaseModel):
    """A games row with its canonical state parsed back into a `GameState`.

    ``map_id`` is the `game_maps` row holding the game's static map, stored apart
    from the state; ``state`` is whole all the same: the repo has rejoined the map.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    scenario: str
    status: str
    state: GameState
    map_id: str
    revision: int = 0
    # How turns advance (WORLD_SPEC §2). ``next_tick_at`` is the next scheduled
    # resolution of a "twice_daily" world; ``None`` means none is scheduled.
    clock: Clock = "manual"
    next_tick_at: datetime | None = None


class PlayerRecord(BaseModel):
    """A players row: a Supabase Auth user bound to one game slot.

    ``bot_policy`` names the AI policy driving this slot (``"world"``: the world
    bot, AI_SPEC §12); ``None`` means a human player. A bot row's ``user_id`` is
    a generated UUID matching no auth user, so the slot can never be claimed or
    driven from outside.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    game_id: str
    slot: str
    user_id: str
    bot_policy: str | None = None


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

    def create_map(
        self,
        game_map: GameMap,
        params: dict[str, Any] | None,
        slots: list[Axial],
    ) -> str:
        """Store ``game_map`` once and return its id.

        ``params`` regenerate the map; ``slots`` are its capitals in fill order.
        Maps stored before road styles existed (early 2026-11-11) have no
        ``road_style`` in ``params``: their roads are ``direct``, not the default.
        """
        ...

    def get_map(self, map_id: str) -> GameMap: ...

    def get_map_slots(self, map_id: str) -> list[Axial]: ...

    def create_game(
        self,
        scenario: str,
        state: GameState,
        *,
        map_id: str,
        clock: Clock = "manual",
        next_tick_at: datetime | None = None,
    ) -> GameRecord:
        """Insert a games row for ``state``, stored without its ``map_id`` map."""
        ...

    def get_game(self, game_id: str) -> GameRecord | None: ...

    def set_game_state(
        self,
        game: GameRecord,
        state: GameState,
        status: str,
        *,
        next_tick_at: datetime | None | Keep = KEEP,
    ) -> None:
        """Replace ``game``'s canonical state (stored without its map).

        A compare-and-set on ``game.revision``: raises `StaleGame` if the game
        was written since ``game`` was read. ``next_tick_at`` moves the world's
        next scheduled tick in the same write (`KEEP` leaves it alone).
        """
        ...

    def list_worlds(self) -> list[WorldRow]:
        """Every active world, newest first, without loading states."""
        ...

    def list_due_worlds(self, now: datetime) -> list[str]:
        """Ids of active scheduled worlds whose next tick is at or before ``now``,
        earliest first."""
        ...

    def create_player(
        self, game_id: str, slot: str, user_id: str, bot_policy: str | None = None
    ) -> PlayerRecord: ...

    def list_players(self, game_id: str) -> list[PlayerRecord]: ...

    def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None: ...

    def get_active_player_for_user(self, user_id: str) -> PlayerRecord | None: ...

    def replace_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        """Store the player's submission for ``turn``, replacing any earlier one."""
        ...

    def list_turn_orders(self, game_id: str, turn: int) -> list[OrdersRecord]: ...

    def record_turn(
        self,
        game: GameRecord,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None: ...

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None: ...

    def delete_world(self, game_id: str, map_id: str) -> None:
        """Delete a world game (its players, orders and turns cascade) and, once
        no other game uses it, its ``map_id`` map."""
        ...


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


def state_blob(state: GameState) -> dict[str, Any]:
    """The JSON stored for ``state``: everything but its map, which has a row."""
    return state.model_dump(mode="json", exclude={"map"})


def join_state(blob: dict[str, Any], game_map: GameMap) -> GameState:
    """Parse a stored state, rejoining its stored-apart ``game_map``.

    The map is passed as the decoded instance, which pydantic keeps as is, so
    every state of a world shares one map object rather than re-validating it.
    """
    return GameState.model_validate({**blob, "map": game_map})


class MapCache:
    """Decoded maps by `game_maps` id, kept for the life of the process.

    A map row is immutable, so a cached map never goes stale. The cache is
    shared by every request's repo (one per process) and bounded, because a
    decoded world map holds ~10^5 hex objects (~100 MB).
    """

    def __init__(self, capacity: int = 4) -> None:
        self._capacity = capacity
        self._maps: OrderedDict[str, GameMap] = OrderedDict()

    def get(self, map_id: str) -> GameMap | None:
        """The cached map for ``map_id``, or ``None`` if it is not cached."""
        game_map = self._maps.get(map_id)
        if game_map is not None:
            self._maps.move_to_end(map_id)
        return game_map

    def put(self, map_id: str, game_map: GameMap) -> None:
        """Cache ``game_map`` under ``map_id``, evicting the least recently used."""
        self._maps[map_id] = game_map
        self._maps.move_to_end(map_id)
        while len(self._maps) > self._capacity:
            self._maps.popitem(last=False)

    def evict(self, map_id: str) -> None:
        """Drop ``map_id`` from the cache (a no-op if it is not cached)."""
        self._maps.pop(map_id, None)


MAP_CACHE = MapCache()
"""The process-wide map cache that `SupabaseGameRepo` uses by default."""


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

    def __init__(self, client: Client, maps: MapCache = MAP_CACHE) -> None:
        self._client = client
        self._maps = maps

    def create_map(
        self,
        game_map: GameMap,
        params: dict[str, Any] | None,
        slots: list[Axial],
    ) -> str:
        compact = encode_map(game_map)
        response = (
            self._client.table("game_maps")
            .insert(
                {
                    **compact.model_dump(mode="json"),
                    "params": params,
                    "slots": [c.model_dump() for c in slots],
                }
            )
            .execute()
        )
        map_id = cast(str, _one_row(response, "create_map")["id"])
        self._maps.put(map_id, game_map)
        return map_id

    def get_map(self, map_id: str) -> GameMap:
        cached = self._maps.get(map_id)
        if cached is not None:
            return cached
        response = (
            self._client.table("game_maps")
            .select("width, height, terrain, roads, sites")
            .eq("id", map_id)
            .execute()
        )
        row = _one_row(response, f"get_map({map_id})")
        game_map = decode_map(CompactMap.model_validate(row))
        self._maps.put(map_id, game_map)
        return game_map

    def get_map_slots(self, map_id: str) -> list[Axial]:
        response = (
            self._client.table("game_maps").select("slots").eq("id", map_id).execute()
        )
        row = _one_row(response, f"get_map_slots({map_id})")
        return [Axial.model_validate(c) for c in row["slots"]]

    def _game_record(self, row: dict[str, Any]) -> GameRecord:
        """Build a `GameRecord` from a games row, rejoining a stored-apart map."""
        return GameRecord(
            id=row["id"],
            scenario=row["scenario"],
            status=row["status"],
            state=join_state(row["state"], self.get_map(row["map_id"])),
            map_id=row["map_id"],
            revision=row["revision"],
            clock=row["clock"],
            next_tick_at=row["next_tick_at"],
        )

    def create_game(
        self,
        scenario: str,
        state: GameState,
        *,
        map_id: str,
        clock: Clock = "manual",
        next_tick_at: datetime | None = None,
    ) -> GameRecord:
        response = (
            self._client.table("games")
            .insert(
                {
                    "name": scenario,
                    "scenario": scenario,
                    "status": "lobby",
                    "state": state_blob(state),
                    "map_id": map_id,
                    "clock": clock,
                    "next_tick_at": (
                        None if next_tick_at is None else next_tick_at.isoformat()
                    ),
                }
            )
            .execute()
        )
        row = _one_row(response, "create_game")
        return self._game_record(row)

    def get_game(self, game_id: str) -> GameRecord | None:
        response = (
            self._client.table("games")
            .select(
                "id, scenario, status, state, map_id, revision, clock, next_tick_at"
            )
            .eq("id", game_id)
            .execute()
        )
        rows = _rows(response)
        if not rows:
            return None
        row = _one_row(response, "get_game")
        return self._game_record(row)

    def set_game_state(
        self,
        game: GameRecord,
        state: GameState,
        status: str,
        *,
        next_tick_at: datetime | None | Keep = KEEP,
    ) -> None:
        update: dict[str, Any] = {
            "state": state_blob(state),
            "status": status,
            "revision": game.revision + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not isinstance(next_tick_at, Keep):
            update["next_tick_at"] = (
                None if next_tick_at is None else next_tick_at.isoformat()
            )
        response = (
            self._client.table("games")
            .update(update)
            .eq("id", game.id)
            .eq("revision", game.revision)
            .execute()
        )
        if not _rows(response):
            raise StaleGame(f"game {game.id} changed since revision {game.revision}")
        _one_row(response, "set_game_state")

    def list_worlds(self) -> list[WorldRow]:
        response = (
            self._client.table("games")
            .select("id, map_id, turn:state->turn, clock, next_tick_at")
            .eq("status", "active")
            .order("created_at", desc=True)
            .execute()
        )
        return [WorldRow.model_validate(row) for row in _rows(response)]

    def list_due_worlds(self, now: datetime) -> list[str]:
        response = (
            self._client.table("games")
            .select("id")
            .eq("clock", "twice_daily")
            .eq("status", "active")
            .lte("next_tick_at", now.isoformat())
            .order("next_tick_at")
            .execute()
        )
        return [cast(str, row["id"]) for row in _rows(response)]

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
        """Return the user's single un-finished player row (their active world).

        Joins ``games`` so a player row from a finished game does not count: a user
        is "active" only while they hold a slot in a world that is not finished.
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

    def replace_orders(
        self, game_id: str, player_id: str, turn: int, orders: dict[str, Any]
    ) -> None:
        response = (
            self._client.table("turn_orders")
            .upsert(
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "turn": turn,
                    "orders": orders,
                    "locked": True,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="game_id,player_id,turn",
            )
            .execute()
        )
        _one_row(response, "replace_orders")

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
        game: GameRecord,
        turn: int,
        events: list[dict[str, Any]],
        state_after: GameState,
        victory: VictoryStatus,
    ) -> None:
        response = (
            self._client.table("turns")
            .insert(
                {
                    "game_id": game.id,
                    "turn": turn,
                    "events": events,
                    "state_after": state_blob(state_after),
                    "victory": victory.model_dump(mode="json"),
                }
            )
            .execute()
        )
        _one_row(response, "record_turn")

    def get_turn(self, game_id: str, turn: int) -> TurnRecord | None:
        response = (
            self._client.table("turns")
            .select("turn, events, state_after, victory, games!inner(map_id)")
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
            state_after=join_state(
                row["state_after"], self.get_map(row["games"]["map_id"])
            ),
            victory=VictoryStatus.model_validate(row["victory"]),
        )

    def delete_world(self, game_id: str, map_id: str) -> None:
        # players, turn_orders and turns cascade from games (on delete cascade).
        self._client.table("games").delete().eq("id", game_id).execute()
        still_used = (
            self._client.table("games").select("id").eq("map_id", map_id).limit(1)
        )
        if not _rows(still_used.execute()):
            self._client.table("game_maps").delete().eq("id", map_id).execute()
            self._maps.evict(map_id)
