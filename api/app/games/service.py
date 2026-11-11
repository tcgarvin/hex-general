"""Turn lifecycle orchestration (roadmap Phase 5).

`GameService` is the application layer between the HTTP routes and the two things
it coordinates: the persistence boundary (`GameRepo`) and the pure execution
engine (`app.engine`). It owns the lifecycle rules — who may join, when a turn
resolves, when a game ends — but holds no I/O itself beyond what the repo does, so
it is unit-testable against an in-memory fake repo.

The central rule: a turn resolves exactly when *every* player in the game has a
locked submission for the current turn. The submission that completes the set
triggers turn resolution synchronously, records the resolved `turns` row, and
advances `games.state`. Victory ends the game (status ``finished``).

Domain failures are raised as typed `GameError` subclasses; the route layer maps
each to an HTTP status. Order validation happens in `validation.py` before a
submission is ever stored, so a stored submission is always individually legal and
a completed set is expected to resolve without the engine raising.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import TypeAdapter

from app.ai.arena import REGISTRY as BOT_REGISTRY
from app.ai.world import WorldPolicy
from app.engine.execution import resolve_execution
from app.engine.events import Event
from app.engine.hex_coords import clear_geometry_caches
from app.engine.fog import filter_events, redact_state
from app.engine.map_codec import CompactMap, encode_map
from app.engine.models import EconomyOrders, GameState, Order, Recruitment
from app.engine.visibility import visible_hexes
from app.engine.scenario import load_scenario_file
from app.engine.victory import VictoryStatus, victory_status
from app.games.repo import (
    KEEP,
    Clock,
    GameRecord,
    GameRepo,
    Keep,
    PlayerRecord,
    StaleGame,
    TurnRecord,
)
from app.games.schemas import (
    DEFAULT_BOT_POLICY,
    ActiveGame,
    DispatchView,
    GameHistoryEntry,
    GameView,
    PlayerCredentials,
    SubmitOrdersRequest,
    SubmitResult,
    TurnReplay,
    WorldReplay,
    WorldSummary,
    WorldState,
    WorldView,
)
from app.games.validation import validate_submission
from app.world.clock import next_tick_after
from app.world.generate import WorldParams, generate
from app.world.seating import WorldFull, seat_player, slot_id

# Bytes of entropy behind a game's invite token (token_urlsafe ~1.3 chars/byte).
_INVITE_TOKEN_BYTES = 24

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
_SCENARIO_NAME = re.compile(r"^[a-z0-9_]+$")

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
    """Every scenario slot is already claimed; no room to join."""


class GameAlreadyActive(GameError):
    """The user already holds a slot in an un-finished game (one active game each)."""


class InvalidInvite(GameError):
    """The supplied invite token does not match the game's."""


class GameNotActive(GameError):
    """The game is not accepting orders (still in lobby, or already finished)."""


class TurnClosed(GameNotActive):
    """A world's turn closed (it is due or just resolved) before the orders landed.

    The orders were not counted; the player should re-plan for the new turn.
    """


class AlreadySubmitted(GameError):
    """This player already has a locked submission for the current turn."""


class ReplayNotFound(GameError):
    """No resolved turn with the requested number exists for this game."""


class UnknownBotPolicy(GameError):
    """The requested bot policy name is not in the AI registry (AI_SPEC §9)."""


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


class NotAWorld(GameError):
    """A world-only operation (join a slot, tick) was asked of a v0.1 match."""


class GameService:
    """Coordinates persistence and the engine across the turn lifecycle."""

    def __init__(self, repo: GameRepo, now: Callable[[], datetime] = _utc_now) -> None:
        self._repo = repo
        self._now = now

    def create_game(
        self,
        scenario: str,
        user_id: str,
        *,
        vs_bot: bool = False,
        bot_policy: str = DEFAULT_BOT_POLICY,
    ) -> PlayerCredentials:
        """Create a game from ``scenario`` and claim its first slot for ``user_id``.

        With ``vs_bot`` (AI_SPEC §9) every remaining slot is filled by a bot
        player — a row whose ``user_id`` is a fresh UUID matching no auth user and
        whose ``bot_policy`` names the AI — and the game starts immediately.
        """
        if vs_bot and bot_policy not in BOT_REGISTRY:
            raise UnknownBotPolicy(
                f"no bot policy named {bot_policy!r}; known: {sorted(BOT_REGISTRY)}"
            )
        self._require_no_active_game(user_id)
        state = _load_scenario_state(scenario)
        invite_token = secrets.token_urlsafe(_INVITE_TOKEN_BYTES)
        game = self._repo.create_game(scenario, state, invite_token)
        first_slot = state.players[0].id
        player = self._repo.create_player(game.id, first_slot, user_id)

        status = game.status
        if vs_bot:
            for slot in (p.id for p in state.players[1:]):
                self._repo.create_player(
                    game.id, slot, str(uuid.uuid4()), bot_policy=bot_policy
                )
            status = "active"
            self._repo.set_game_state(game, state, status)
        return PlayerCredentials(
            game_id=game.id,
            slot=player.slot,
            status=status,
            invite_token=game.invite_token,
        )

    def join_game(
        self, game_id: str, user_id: str, invite_token: str
    ) -> PlayerCredentials:
        """Claim the next open slot for ``user_id``, gated by the invite token.

        Activates the game once every slot is filled.
        """
        game = self._require_game(game_id)
        if not secrets.compare_digest(invite_token, game.invite_token):
            raise InvalidInvite(f"invalid invite token for game {game_id}")
        self._require_no_active_game(user_id)

        present = {p.slot for p in self._repo.list_players(game_id)}
        open_slots = [p.id for p in game.state.players if p.id not in present]
        if not open_slots:
            raise GameFull(f"game {game_id} has no open slots")

        slot = open_slots[0]
        player = self._repo.create_player(game_id, slot, user_id)

        status = game.status
        if len(present) + 1 == len(game.state.players):
            status = "active"
            self._repo.set_game_state(game, game.state, status)
        return PlayerCredentials(
            game_id=game_id,
            slot=player.slot,
            status=status,
            invite_token=game.invite_token,
        )

    def get_dispatch(self, user_id: str) -> DispatchView:
        """Return where login should send ``user_id``: their active game, or none."""
        player = self._repo.get_active_player_for_user(user_id)
        if player is None:
            return DispatchView(active_game=None)
        kind: Literal["match", "world"] = (
            "world" if self._repo.is_world_game(player.game_id) else "match"
        )
        return DispatchView(
            active_game=ActiveGame(game_id=player.game_id, slot=player.slot, kind=kind)
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

    def get_history(self, user_id: str) -> list[GameHistoryEntry]:
        """Return ``user_id``'s games (newest first) with a per-player outcome.

        Outcome is derived from the authoritative victory check over each game's
        stored state, mapped to the caller's slot: win/loss/draw once decided, else
        ``in_progress`` (a lobby or active game).
        """
        records = self._repo.list_user_games(user_id)
        entries = [
            GameHistoryEntry(
                game_id=record.game_id,
                scenario=record.scenario,
                status=record.status,
                your_slot=record.slot,
                turn=record.state.turn,
                outcome=_outcome_for(victory_status(record.state), record.slot),
                created_at=record.created_at,
                vs_bot=record.bot_policy is not None,
                bot_policy=record.bot_policy,
            )
            for record in records
        ]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)

    def get_view(self, player: PlayerRecord) -> GameView:
        """Return ``player``'s fog-filtered view of their game (SPEC §9)."""
        game = self._require_game(player.game_id)
        players = self._repo.list_players(player.game_id)
        slot_by_player_id = {p.id: p.slot for p in players}
        slots_present = sorted(p.slot for p in players)
        # The caller is human (bots never call the API), so any bot row is the
        # opponent. A game has at most one bot (AI_SPEC §9).
        opponent_bot_policy = next(
            (p.bot_policy for p in players if p.bot_policy is not None), None
        )
        submitted = {
            slot_by_player_id[o.player_id]
            for o in self._repo.list_turn_orders(player.game_id, game.state.turn)
        }
        locked = {slot: slot in submitted for slot in slots_present}
        # The invite link is only useful while a slot is still open (lobby); once
        # the game is active there is nothing to share, so hide it.
        invite_token = game.invite_token if game.status == "lobby" else None
        # Victory is a global fact (settlement control), so it is computed from the
        # authoritative full state; only the returned `state` is fogged.
        return GameView(
            game_id=game.id,
            status=game.status,
            turn=game.state.turn,
            your_slot=player.slot,
            slots_present=slots_present,
            locked=locked,
            state=redact_state(game.state, player.slot),
            visible=sorted(
                visible_hexes(game.state, player.slot), key=lambda c: (c.q, c.r)
            ),
            victory=victory_status(game.state),
            invite_token=invite_token,
            vs_bot=opponent_bot_policy is not None,
            bot_policy=opponent_bot_policy,
        )

    def submit_orders(
        self,
        player: PlayerRecord,
        orders: list[Order],
        recruitments: list[Recruitment],
        economy_orders: EconomyOrders | None = None,
    ) -> SubmitResult:
        """Validate and lock ``player``'s submission; resolve the turn if it's last.

        ``economy_orders`` are a world player's base orders (build, disband pick).
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
        if game.is_world:
            # A world player may revise their plan until the tick resolves it.
            # Once a scheduled turn is due it is closed, so nothing lands while it
            # resolves; and a re-read catches a tick that won the race anyway.
            due = game.next_tick_at
            if game.clock == "twice_daily" and due is not None and due <= self._now():
                raise TurnClosed(f"turn {turn} is resolving; resubmit for the next")
            self._repo.replace_orders(game.id, player.id, turn, submission)
            if self._require_game(game.id).state.turn != turn:
                raise TurnClosed(f"turn {turn} resolved first; resubmit for the next")
            return SubmitResult(
                locked=True, executed=False, turn=turn, status=game.status
            )
        already = {o.player_id for o in self._repo.list_turn_orders(game.id, turn)}
        if player.id in already:
            raise AlreadySubmitted(
                f"{player.slot!r} already submitted orders for turn {turn}"
            )

        self._repo.record_orders(game.id, player.id, turn, submission)

        players = self._repo.list_players(game.id)
        self._submit_bot_orders(game, players, turn)
        submissions = self._repo.list_turn_orders(game.id, turn)
        if len(submissions) < len(players):
            return SubmitResult(
                locked=True, executed=False, turn=turn, status=game.status
            )

        next_state, status = self._resolve_turn(game, players)
        return SubmitResult(
            locked=True, executed=True, turn=next_state.turn, status=status
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
            secrets.token_urlsafe(_INVITE_TOKEN_BYTES),
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
        lose a write. The loser re-reads and seats in the next open slot.
        """
        self._require_world(game_id)
        self._require_no_active_game(user_id)
        game, slot = _retry_stale(lambda: self._seat(game_id))
        player = self._repo.create_player(game.id, slot, user_id)
        return PlayerCredentials(
            game_id=game.id,
            slot=player.slot,
            status=game.status,
            invite_token=game.invite_token,
        )

    def _seat(self, game_id: str) -> tuple[GameRecord, str]:
        """One attempt at seating a newcomer in a fresh read of the world."""
        game = self._require_world(game_id)
        try:
            state, slot = seat_player(
                game.state, self._repo.get_map_slots(_map_id(game))
            )
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
                self._require_world(game_id), next_tick_at=next_tick_at
            )
        )
        return SubmitResult(
            locked=True, executed=True, turn=next_state.turn, status=status
        )

    def delete_world(self, game_id: str) -> None:
        """Delete a world and everything stored for it (dev-only teardown)."""
        game = self._require_world(game_id)
        self._repo.delete_world(game.id, _map_id(game))

    def get_world_view(self, player: PlayerRecord) -> WorldView:
        """``player``'s fogged view of their world, without the map."""
        game = self._require_world(player.game_id)
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
            map_id=_map_id(game),
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
        game = self._require_world(player.game_id)
        return encode_map(game.state.map)

    def get_world_replay(self, player: PlayerRecord, turn: int) -> WorldReplay:
        """``player``'s fogged replay of world turn ``turn``, without the map."""
        self._require_world(player.game_id)
        record, events, fogged = self._fogged_turn(player, turn)
        return WorldReplay(
            turn=record.turn, events=events, state_after=WorldState.of(fogged)
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
        game = self._require_world(game_id)
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
            game = self._require_world(game_id)
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
        try:
            bot_orders = self._world_bot_orders(game, players)
            return self._resolve_turn(
                game, players, extra=bot_orders, next_tick_at=next_tick_at
            )
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

    def resign(self, player: PlayerRecord) -> GameView:
        """Concede ``player``'s slot, ending the game if they were the last rival.

        Only valid while the game is active (a lobby has no game to forfeit; a
        finished game is already decided — both raise `GameNotActive`). Resignation
        is recorded in the canonical state, so the recomputed victory naturally
        awards the win to the last remaining contender.
        """
        game = self._require_game(player.game_id)
        if game.status != "active":
            raise GameNotActive(f"game {game.id} is {game.status}; cannot resign")

        new_state = game.state.model_copy(
            update={"resigned": [*game.state.resigned, player.slot]}
        )
        victory = victory_status(new_state)
        status = "finished" if victory.decided else "active"
        self._repo.set_game_state(game, new_state, status)
        return self.get_view(player)

    def get_replay(self, player: PlayerRecord, turn: int) -> TurnReplay:
        """Return ``player``'s fog-filtered replay for ``turn`` (SPEC §9, §16.2).

        The viewer sees the resulting state with hidden enemy units removed and
        only the events they could witness — combat and movement on hexes they can
        see, plus anything involving their own units.
        """
        record, events, fogged = self._fogged_turn(player, turn)
        return TurnReplay(
            turn=record.turn,
            events=events,
            state_after=fogged,
            victory=record.victory,
        )

    def _fogged_turn(
        self, player: PlayerRecord, turn: int
    ) -> tuple[TurnRecord, list[Event], GameState]:
        """Resolved ``turn`` as ``player`` saw it: witnessed events, fogged state."""
        record = self._repo.get_turn(player.game_id, turn)
        if record is None:
            raise ReplayNotFound(f"game {player.game_id} has no resolved turn {turn}")
        events = _EVENTS.validate_python(record.events)
        witnessed = filter_events(events, record.state_after, player.slot)
        return record, witnessed, redact_state(record.state_after, player.slot)

    def _submit_bot_orders(
        self, game: GameRecord, players: list[PlayerRecord], turn: int
    ) -> None:
        """Plan and lock a submission for every bot slot still open this turn.

        Runs after a human submission is stored (AI_SPEC §9), so a bot never
        blocks a turn: once the last human is in, every bot's plan is computed
        synchronously — fog-redacted view in, `Policy.plan` out — and validated
        by the same `validate_submission` path as a human body (no trust
        shortcut) before being stored like any other submission.
        """
        bots = [(p, p.bot_policy) for p in players if p.bot_policy is not None]
        if not bots:
            return
        submitted = {o.player_id for o in self._repo.list_turn_orders(game.id, turn)}
        for bot, policy_name in bots:
            if bot.id in submitted:
                continue
            policy = BOT_REGISTRY[policy_name](turn)
            view = redact_state(game.state, bot.slot)
            plan = policy.plan(view, bot.slot)
            validate_submission(game.state, bot.slot, plan.orders, plan.recruitments)
            self._repo.record_orders(
                game.id,
                bot.id,
                turn,
                {
                    "orders": [o.model_dump(mode="json") for o in plan.orders],
                    "recruitments": [
                        r.model_dump(mode="json") for r in plan.recruitments
                    ],
                },
            )

    def _resolve_turn(
        self,
        game: GameRecord,
        players: list[PlayerRecord],
        extra: _Planned | None = None,
        next_tick_at: datetime | None | Keep = KEEP,
    ) -> tuple[GameState, str]:
        """Run the engine over the full set of submissions and persist the result.

        Resolution goes through `resolve_execution`, which owns mooted-intent
        semantics: it drops intents the turn's own simultaneous dynamics make moot
        — an order for a unit destroyed by pre-movement barrage, or a recruit at a
        city lost/occupied/unaffordable mid-turn (possible under fog even though
        each submission validated statically) — resolving them as no-ops with a
        replay event rather than crashing the turn.

        ``extra`` are orders planned at resolution rather than stored: a world's
        bots plan when the turn ticks.
        """
        turn = game.state.turn
        slot_by_player_id = {p.id: p.slot for p in players}
        submissions = sorted(
            self._repo.list_turn_orders(game.id, turn),
            key=lambda o: slot_by_player_id[o.player_id],
        )

        planned = extra if extra is not None else _Planned()
        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        economy: list[EconomyOrders] = []
        for submission in submissions:
            orders.extend(_ORDERS.validate_python(submission.orders["orders"]))
            recruitments.extend(
                _RECRUITMENTS.validate_python(submission.orders["recruitments"])
            )
            # Rows stored before base orders existed have no "economy" key.
            stored_economy = submission.orders.get("economy")
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

    def _require_world(self, game_id: str) -> GameRecord:
        game = self._require_game(game_id)
        if not game.is_world:
            raise NotAWorld(f"game {game_id} is a v0.1 match, not a world")
        return game

    def _require_no_active_game(self, user_id: str) -> None:
        """Enforce one active game per user before a create/join (SPEC: Phase 7)."""
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


def _map_id(game: GameRecord) -> str:
    """The map id of a world game (`GameService._require_world` checked it has one)."""
    if game.map_id is None:
        raise NotAWorld(f"game {game.id} has no stored map")
    return game.map_id


def _outcome_for(victory: VictoryStatus, slot: str) -> str:
    """Map a victory check to ``slot``'s outcome label for the history screen."""
    if not victory.decided:
        return "in_progress"
    if victory.winner is None:
        return "draw"
    return "win" if victory.winner == slot else "loss"


def _load_scenario_state(scenario: str) -> GameState:
    """Load a turn-0 `GameState` from a named scenario file (boundary-validated).

    The name is restricted to ``[a-z0-9_]`` so it cannot escape the scenarios
    directory; an unknown scenario raises `GameNotFound`.
    """
    if not _SCENARIO_NAME.match(scenario):
        raise GameNotFound(f"invalid scenario name {scenario!r}")
    path = _SCENARIOS_DIR / f"{scenario}.json"
    if not path.is_file():
        raise GameNotFound(f"no scenario named {scenario!r}")
    return load_scenario_file(path)


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
