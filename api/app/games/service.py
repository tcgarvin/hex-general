"""World game lifecycle orchestration (WORLD_SPEC §10.1).

`GameService` is the application layer between the HTTP routes and the two things
it coordinates: the persistence boundary (`GameRepo`) and the pure execution
engine (`app.engine`). It owns the lifecycle rules — who may join, when a turn
resolves, when a game ends — but holds no I/O itself beyond what the repo does, so
it is unit-testable against an in-memory fake repo.

A world's turn resolves on its clock, not when everyone is in: players store (and
may revise) their plans until the tick, which plans for the bots, runs the engine
over every submission, records the resolved `turns` row and advances
`games.state`. Victory ends the game (status ``finished``). State writes are
compare-and-set, so a join landing during a tick, or two overlapping ticks,
retry from a fresh read instead of overwriting each other.

Domain failures are raised as typed `GameError` subclasses; the route layer maps
each to an HTTP status. Order validation happens in `validation.py` before a
submission is ever stored, so a stored submission is always individually legal and
a completed set is expected to resolve without the engine raising.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import TypeAdapter

from app.ai.world import WorldPolicy
from app.engine.events import Event
from app.engine.execution import resolve_execution
from app.engine.fog import filter_events, redact_state
from app.engine.hex_coords import clear_geometry_caches
from app.engine.map_codec import CompactMap, encode_map
from app.engine.models import EconomyOrders, GameState, Order, Recruitment
from app.engine.victory import victory_status
from app.engine.visibility import visible_hexes
from app.games.repo import (
    KEEP,
    Clock,
    GameRecord,
    GameRepo,
    Keep,
    PlayerRecord,
    StaleGame,
)
from app.games.schemas import (
    ActiveWorld,
    ActiveWorldView,
    AdminWorld,
    AdminWorldList,
    ClockSchedule,
    PlayerCredentials,
    SubmitOrdersRequest,
    SubmitResult,
    WorldReplay,
    WorldState,
    WorldSummary,
    WorldView,
)
from app.games.validation import validate_submission
from app.world.clock import TICK_HOURS, TICK_ZONE, next_tick_after
from app.world.collector import quiet_collector
from app.world.generate import WorldParams, generate
from app.world.seating import WorldFull, seat_player, slot_id, unseat_player

_ORDERS = TypeAdapter(list[Order])
_RECRUITMENTS = TypeAdapter(list[Recruitment])
_EVENTS = TypeAdapter(list[Event])


@dataclasses.dataclass
class _Planned:
    """Submissions planned at resolution rather than stored (a world's bots)."""

    orders: list[Order] = dataclasses.field(default_factory=list)
    recruitments: list[Recruitment] = dataclasses.field(default_factory=list)
    economy: list[EconomyOrders] = dataclasses.field(default_factory=list)


_T = TypeVar("_T")

WORLD_SCENARIO = "world"
"""The ``games.scenario`` label of a generated world."""

WORLD_BOT_POLICY = "world"
"""The ``players.bot_policy`` of a world bot (`WorldPolicy`, AI_SPEC §12)."""


class GameError(Exception):
    """Base class for turn-lifecycle domain failures (each maps to an HTTP code)."""


class GameNotFound(GameError):
    """No game exists with the requested id."""


class GameFull(GameError):
    """Every slot of the world is already taken; no room to join."""


class GameAlreadyActive(GameError):
    """The user already holds a slot in an un-finished game (one active game each)."""


class GameNotActive(GameError):
    """The game is not accepting orders or ticks (it is finished)."""


class TurnClosed(GameNotActive):
    """A world's turn closed (it is due or just resolved) before the orders landed.

    The orders were not counted; the player should re-plan for the new turn.
    """


class ReplayNotFound(GameError):
    """No resolved turn with the requested number exists for this game."""


class WorldTicksFailed(GameError):
    """Some due worlds failed to resolve on a clock run (the rest did)."""

    def __init__(self, failed: dict[str, str]) -> None:
        super().__init__(f"{len(failed)} world(s) failed to tick: {failed}")
        self.failed = failed


@dataclasses.dataclass(frozen=True)
class ClockRun:
    """What one scheduler call did: the worlds it resolved and those already done."""

    ticked: list[str]
    skipped: list[str]


_log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GameService:
    """Coordinates persistence and the engine across the turn lifecycle."""

    def __init__(self, repo: GameRepo, now: Callable[[], datetime] = _utc_now) -> None:
        self._repo = repo
        self._now = now

    def get_active_world(self, user_id: str) -> ActiveWorldView:
        """Where login should send ``user_id``: the world they hold a slot in, or
        none."""
        player = self._repo.get_active_player_for_user(user_id)
        if player is None:
            return ActiveWorldView(active_world=None)
        return ActiveWorldView(
            active_world=ActiveWorld(game_id=player.game_id, slot=player.slot)
        )

    def list_worlds(self) -> list[WorldSummary]:
        """Every running world, newest first, with how full it is."""
        summaries = []
        for row in self._repo.list_worlds():
            players = self._repo.list_players(row.id)
            summaries.append(
                WorldSummary(
                    game_id=row.id,
                    turn=row.turn,
                    slots=len(self._repo.get_map_slots(row.map_id)),
                    players=len(players),
                    humans=sum(1 for p in players if p.bot_policy is None),
                    clock=row.clock,
                    next_tick_at=row.next_tick_at,
                )
            )
        return summaries

    def submit_orders(
        self,
        player: PlayerRecord,
        orders: list[Order],
        recruitments: list[Recruitment],
        economy_orders: EconomyOrders | None = None,
    ) -> SubmitResult:
        """Validate and store ``player``'s plan for the current turn.

        The plan resolves at the world's next tick, and the player may revise it
        until then (a later submission replaces the earlier one). Once a
        scheduled turn is due it is closed, so nothing lands while it resolves;
        a re-read after storing catches a tick that won the race anyway. Either
        way the plan is refused with `TurnClosed`. ``economy_orders`` are the
        player's base orders (build, disband pick), ``None`` if they gave none.
        """
        game = self._require_game(player.game_id)
        if game.status != "active":
            raise GameNotActive(
                f"game {game.id} is {game.status}, not accepting orders"
            )

        validate_submission(
            game.state, player.slot, orders, recruitments, economy_orders
        )

        turn = game.state.turn
        submission = {
            "orders": [o.model_dump(mode="json") for o in orders],
            "recruitments": [r.model_dump(mode="json") for r in recruitments],
            "economy": (
                None
                if economy_orders is None
                else economy_orders.model_dump(mode="json")
            ),
        }
        due = game.next_tick_at
        if game.clock == "twice_daily" and due is not None and due <= self._now():
            raise TurnClosed(f"turn {turn} is resolving; resubmit for the next")
        self._repo.replace_orders(game.id, player.id, turn, submission)
        if self._require_game(game.id).state.turn != turn:
            raise TurnClosed(f"turn {turn} resolved first; resubmit for the next")
        return SubmitResult(locked=True, executed=False, turn=turn, status=game.status)

    def list_admin_worlds(self) -> AdminWorldList:
        """Every world whatever its status, newest first, for the admin screen,
        with the scheduled clock's hours."""
        return AdminWorldList(
            worlds=[
                AdminWorld(
                    game_id=row.id,
                    status=row.status,
                    turn=row.turn,
                    slots=row.slots,
                    width=row.width,
                    height=row.height,
                    humans=row.humans,
                    bots=row.bots,
                    open=row.slots - row.humans - row.bots,
                    seed=row.seed,
                    clock=row.clock,
                    next_tick_at=row.next_tick_at,
                    created_at=row.created_at,
                )
                for row in self._repo.list_admin_worlds()
            ],
            schedule=ClockSchedule(zone=TICK_ZONE.key, hours=sorted(TICK_HOURS)),
        )

    def create_world(
        self, slots: int, seed: int, bots: int, clock: Clock = "manual"
    ) -> str:
        """Generate a ``slots``-slot world, seat ``bots`` bots, return its game id.

        The map is stored once (WORLD_SPEC §10.1) and the game starts active: a
        world has no lobby, and players join a running world with `join_world`.
        A ``"twice_daily"`` world is scheduled for the next 01:00 or 13:00 New
        York tick; a ``"manual"`` one only moves when ticked.
        """
        params = WorldParams.for_slots(slots, seed=seed)
        world = generate(params)
        bot_ids = [slot_id(k) for k in range(bots)]
        state = world.to_state(bot_ids)
        map_id = self._repo.create_map(
            world.game_map, dataclasses.asdict(params), world.slots
        )
        game = self._repo.create_game(
            WORLD_SCENARIO,
            state,
            map_id=map_id,
            clock=clock,
            next_tick_at=(
                next_tick_after(self._now()) if clock == "twice_daily" else None
            ),
        )
        for bot in bot_ids:
            self._repo.create_player(
                game.id, bot, str(uuid.uuid4()), bot_policy=WORLD_BOT_POLICY
            )
        self._repo.set_game_state(game, game.state, "active")
        return game.id

    def join_world(self, game_id: str, user_id: str) -> PlayerCredentials:
        """Seat ``user_id`` in the world's first open slot (WORLD_SPEC §10.1).

        The seated state is written first, as a compare-and-set: two people
        joining at once, or a join during a tick, can't take the same slot or
        lose a write. The loser re-reads and seats in the next open slot. The
        players row comes second; if writing it fails, the seat is taken back
        out of the state (`_recover_failed_join`), so a failed join never leaves
        a slot seated in the state with no player behind it.
        """
        self._require_game(game_id)
        self._require_no_active_game(user_id)
        game, slot = _retry_stale(lambda: self._seat(game_id))
        try:
            self._repo.create_player(game.id, slot, user_id)
        except Exception as exc:  # undo the seat, then re-raise (never swallowed)
            self._recover_failed_join(game.id, slot, user_id, exc)
        return PlayerCredentials(game_id=game.id, slot=slot, status=game.status)

    def _recover_failed_join(
        self, game_id: str, slot: str, user_id: str, error: Exception
    ) -> None:
        """Settle a join whose players row insert raised after ``slot`` was seated.

        Returns only if the row was written after all (the call failed after
        the insert landed): the join stands. Otherwise the seat is undone and
        the join fails: with `GameAlreadyActive` if the same user's concurrent
        join (a double click) took another slot in this world first, else with
        the insert's own ``error``. If undoing the seat fails too, that error
        propagates with ``error`` as its context.
        """
        existing = self._repo.get_player(game_id, user_id)
        if existing is not None and existing.slot == slot:
            return
        _retry_stale(lambda: self._unseat(game_id, slot))
        if existing is not None:
            raise GameAlreadyActive(
                f"user already holds slot {existing.slot!r} in game {game_id}"
            ) from error
        raise error

    def _unseat(self, game_id: str, slot: str) -> None:
        """One attempt at taking ``slot`` back out of a fresh read of the world."""
        game = self._require_game(game_id)
        state = unseat_player(game.state, self._repo.get_map_slots(game.map_id), slot)
        self._repo.set_game_state(game, state, game.status)

    def _seat(self, game_id: str) -> tuple[GameRecord, str]:
        """One attempt at seating a newcomer in a fresh read of the world."""
        game = self._require_game(game_id)
        try:
            state, slot = seat_player(game.state, self._repo.get_map_slots(game.map_id))
        except WorldFull as exc:
            raise GameFull(f"world {game_id} has no open slot") from exc
        self._repo.set_game_state(game, state, game.status)
        return game, slot

    def tick_world(
        self, game_id: str, *, next_tick_at: datetime | None | Keep = KEEP
    ) -> SubmitResult:
        """Resolve the world's current turn now (WORLD_SPEC §10.1).

        Bots plan from their fogged views. A human who sent no orders simply has
        none this turn: their standing travel orders still continue.
        ``next_tick_at`` is when the next turn will resolve, for a caller that
        drives the clock itself (the local tick loop); by default a manual tick
        leaves the world's schedule where it was.
        """
        next_state, status = _retry_stale(
            lambda: self._tick_game(
                self._require_game(game_id), next_tick_at=next_tick_at
            )
        )
        return SubmitResult(
            locked=True, executed=True, turn=next_state.turn, status=status
        )

    def delete_world(self, game_id: str) -> None:
        """Delete a world and everything stored for it (admin teardown)."""
        game = self._require_game(game_id)
        self._repo.delete_world(game.id, game.map_id)

    def get_world_view(self, player: PlayerRecord) -> WorldView:
        """``player``'s fogged view of their world, without the map."""
        game = self._require_game(player.game_id)
        mine = [
            o
            for o in self._repo.list_turn_orders(game.id, game.state.turn)
            if o.player_id == player.id
        ]
        return WorldView(
            game_id=game.id,
            status=game.status,
            turn=game.state.turn,
            your_slot=player.slot,
            map_id=game.map_id,
            submission=_submission_request(mine[0].orders) if mine else None,
            state=WorldState.of(redact_state(game.state, player.slot)),
            clock=game.clock,
            next_tick_at=game.next_tick_at,
            visible=sorted(
                visible_hexes(game.state, player.slot), key=lambda c: (c.q, c.r)
            ),
        )

    def get_world_map(self, player: PlayerRecord) -> CompactMap:
        """The static map of ``player``'s world, in the compact grid encoding."""
        game = self._require_game(player.game_id)
        return encode_map(game.state.map)

    def get_world_replay(self, player: PlayerRecord, turn: int) -> WorldReplay:
        """``player``'s fogged replay of world turn ``turn``, without the map."""
        record = self._repo.get_turn(player.game_id, turn)
        if record is None:
            raise ReplayNotFound(f"game {player.game_id} has no resolved turn {turn}")
        events = _EVENTS.validate_python(record.events)
        witnessed = filter_events(events, record.state_after, player.slot)
        fogged = redact_state(record.state_after, player.slot)
        return WorldReplay(
            turn=record.turn, events=witnessed, state_after=WorldState.of(fogged)
        )

    def tick_due_worlds(self) -> ClockRun:
        """Resolve every scheduled world whose tick is due (the scheduler's call).

        Each world's next tick moves on in the same compare-and-set as its new
        state, so a repeated or overlapping call skips a world another call
        already resolved. One world failing doesn't stop the others: every due
        world is tried, then `WorldTicksFailed` names the failures so the
        scheduler retries (the ones that succeeded are no longer due).
        """
        now = self._now()
        ticked: list[str] = []
        skipped: list[str] = []
        failed: dict[str, str] = {}
        for game_id in self._repo.list_due_worlds(now):
            try:
                done = _retry_stale(
                    functools.partial(self._scheduled_tick, game_id, now)
                )
            except Exception as exc:  # isolate one world; re-raised below
                _log.exception("scheduled tick of world %s failed", game_id)
                failed[game_id] = f"{type(exc).__name__}: {exc}"
                continue
            (ticked if done else skipped).append(game_id)
        if failed:
            raise WorldTicksFailed(failed)
        return ClockRun(ticked=ticked, skipped=skipped)

    def _scheduled_tick(self, game_id: str, now: datetime) -> bool:
        """Resolve ``game_id`` if its tick is still due at ``now``; report whether."""
        game = self._require_game(game_id)
        due = game.next_tick_at
        if game.status != "active" or due is None or due > now:
            return False
        self._tick_game(game, next_tick_at=next_tick_after(now))
        return True

    def announce_tick(self, game_id: str, at: datetime | None) -> None:
        """Publish when a manual world's next turn resolves (``None``: unknown).

        For the local tick loop, so players see a countdown. Refused for a
        scheduled world, whose next tick belongs to the scheduled clock.
        """

        def attempt() -> None:
            game = self._require_game(game_id)
            if game.clock != "manual":
                raise GameNotActive(
                    f"world {game_id} runs on the {game.clock} clock; "
                    "its next tick is not announced by hand"
                )
            self._repo.set_game_state(game, game.state, game.status, next_tick_at=at)

        _retry_stale(attempt)

    def _tick_game(
        self, game: GameRecord, *, next_tick_at: datetime | None | Keep
    ) -> tuple[GameState, str]:
        if game.status != "active":
            raise GameNotActive(f"world {game.id} is {game.status}; cannot tick")
        players = self._repo.list_players(game.id)
        # No collector pauses inside the tick: one young pass when it ends.
        with quiet_collector():
            try:
                bot_orders = self._world_bot_orders(game, players)
                return self._resolve_turn(game, players, bot_orders, next_tick_at)
            finally:
                # Bounds memory on a 512 MiB instance: the caches grow ~15 MB/tick.
                clear_geometry_caches()

    def _world_bot_orders(
        self, game: GameRecord, players: list[PlayerRecord]
    ) -> _Planned:
        """Every world bot's plan for this turn, validated like a human's.

        Personalities are seeded from the game id, so a bot keeps its character
        from turn to turn and differs from the same slot in another world.
        """
        policy = WorldPolicy(seed=uuid.UUID(game.id).int % (1 << 31))
        planned = _Planned()
        for bot in (p for p in players if p.bot_policy == WORLD_BOT_POLICY):
            plan = policy.plan(redact_state(game.state, bot.slot), bot.slot)
            economy = plan.economy_orders(bot.slot)
            validate_submission(
                game.state, bot.slot, plan.orders, plan.recruitments, economy
            )
            planned.orders.extend(plan.orders)
            planned.recruitments.extend(plan.recruitments)
            if economy is not None:
                planned.economy.append(economy)
        return planned

    def _resolve_turn(
        self,
        game: GameRecord,
        players: list[PlayerRecord],
        planned: _Planned,
        next_tick_at: datetime | None | Keep,
    ) -> tuple[GameState, str]:
        """Run the engine over the turn's submissions and persist the result.

        Resolution goes through `resolve_execution`, which owns mooted-intent
        semantics: it drops intents the turn's own simultaneous dynamics make moot
        — an order for a unit destroyed by pre-movement barrage, or a recruit at a
        city lost/occupied/unaffordable mid-turn (possible under fog even though
        each submission validated statically) — resolving them as no-ops with a
        replay event rather than crashing the turn.

        The submissions are the humans' stored plans plus ``planned``: the
        bots' orders, planned when the turn ticks rather than stored.
        """
        turn = game.state.turn
        slot_by_player_id = {p.id: p.slot for p in players}
        submissions = sorted(
            self._repo.list_turn_orders(game.id, turn),
            key=lambda o: slot_by_player_id[o.player_id],
        )

        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        economy: list[EconomyOrders] = []
        for submission in submissions:
            orders.extend(_ORDERS.validate_python(submission.orders["orders"]))
            recruitments.extend(
                _RECRUITMENTS.validate_python(submission.orders["recruitments"])
            )
            stored_economy = submission.orders["economy"]
            if stored_economy is not None:
                economy.append(EconomyOrders.model_validate(stored_economy))

        orders.extend(planned.orders)
        recruitments.extend(planned.recruitments)
        economy.extend(planned.economy)
        next_state, events = resolve_execution(
            game.state, orders, recruitments, economy
        )
        victory = victory_status(next_state)
        status = "finished" if victory.decided else "active"

        # State first: it is the compare-and-set, so a turn that lost a race
        # leaves no turns row behind and can be retried.
        self._repo.set_game_state(game, next_state, status, next_tick_at=next_tick_at)
        self._repo.record_turn(
            game,
            turn,
            [event.model_dump(mode="json") for event in events],
            next_state,
            victory,
        )
        return next_state, status

    def _require_game(self, game_id: str) -> GameRecord:
        game = self._repo.get_game(game_id)
        if game is None:
            raise GameNotFound(f"no game with id {game_id}")
        return game

    def _require_no_active_game(self, user_id: str) -> None:
        """Enforce one active game per user before a join (SPEC: Phase 7)."""
        existing = self._repo.get_active_player_for_user(user_id)
        if existing is not None:
            raise GameAlreadyActive(
                f"user already holds slot {existing.slot!r} in game {existing.game_id}"
            )


_STALE_ATTEMPTS = 3
"""How many times a world write is tried against fresh reads before giving up."""


def _retry_stale(attempt: Callable[[], _T]) -> _T:
    """Run ``attempt``, re-running it from scratch if it lost a write race.

    Each attempt re-reads the game, so a retry plans against the state that
    won. After `_STALE_ATTEMPTS` lost races the `StaleGame` propagates.
    """
    for _ in range(_STALE_ATTEMPTS - 1):
        try:
            return attempt()
        except StaleGame:
            continue
    return attempt()


def _submission_request(stored: dict[str, Any]) -> SubmitOrdersRequest:
    """A stored ``turn_orders`` plan, back in the shape it was submitted in.

    The row is read from the database, so it is validated on the way in.
    """
    economy = stored["economy"]
    return SubmitOrdersRequest.model_validate(
        {
            "orders": stored["orders"],
            "recruitments": stored["recruitments"],
            "build": None if economy is None else economy["build"],
            "disband": [] if economy is None else economy["disband"],
        }
    )
