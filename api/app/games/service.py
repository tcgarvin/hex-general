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

import re
import secrets
import uuid
from pathlib import Path

from pydantic import TypeAdapter

from app.ai.arena import REGISTRY as BOT_REGISTRY
from app.ai.simulate import resolve_plan
from app.engine.events import Event
from app.engine.fog import filter_events, redact_state
from app.engine.models import GameState, Order, Recruitment
from app.engine.visibility import visible_hexes
from app.engine.scenario import load_scenario_file
from app.engine.victory import VictoryStatus, victory_status
from app.games.repo import GameRecord, GameRepo, PlayerRecord
from app.games.schemas import (
    DEFAULT_BOT_POLICY,
    ActiveGame,
    DispatchView,
    GameHistoryEntry,
    GameView,
    PlayerCredentials,
    SubmitResult,
    TurnReplay,
)
from app.games.validation import validate_submission

# Bytes of entropy behind a game's invite token (token_urlsafe ~1.3 chars/byte).
_INVITE_TOKEN_BYTES = 24

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
_SCENARIO_NAME = re.compile(r"^[a-z0-9_]+$")

_ORDERS = TypeAdapter(list[Order])
_RECRUITMENTS = TypeAdapter(list[Recruitment])
_EVENTS = TypeAdapter(list[Event])


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


class AlreadySubmitted(GameError):
    """This player already has a locked submission for the current turn."""


class ReplayNotFound(GameError):
    """No resolved turn with the requested number exists for this game."""


class UnknownBotPolicy(GameError):
    """The requested bot policy name is not in the AI registry (AI_SPEC §9)."""


class GameService:
    """Coordinates persistence and the engine across the turn lifecycle."""

    def __init__(self, repo: GameRepo) -> None:
        self._repo = repo

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
            self._repo.set_game_state(game.id, state, status)
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
            self._repo.set_game_state(game_id, game.state, status)
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
        return DispatchView(
            active_game=ActiveGame(game_id=player.game_id, slot=player.slot)
        )

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
        )

    def submit_orders(
        self,
        player: PlayerRecord,
        orders: list[Order],
        recruitments: list[Recruitment],
    ) -> SubmitResult:
        """Validate and lock ``player``'s submission; resolve the turn if it's last."""
        game = self._require_game(player.game_id)
        if game.status != "active":
            raise GameNotActive(
                f"game {game.id} is {game.status}, not accepting orders"
            )

        validate_submission(game.state, player.slot, orders, recruitments)

        turn = game.state.turn
        already = {o.player_id for o in self._repo.list_turn_orders(game.id, turn)}
        if player.id in already:
            raise AlreadySubmitted(
                f"{player.slot!r} already submitted orders for turn {turn}"
            )

        self._repo.record_orders(
            game.id,
            player.id,
            turn,
            {
                "orders": [o.model_dump(mode="json") for o in orders],
                "recruitments": [r.model_dump(mode="json") for r in recruitments],
            },
        )

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
        self._repo.set_game_state(game.id, new_state, status)
        return self.get_view(player)

    def get_replay(self, player: PlayerRecord, turn: int) -> TurnReplay:
        """Return ``player``'s fog-filtered replay for ``turn`` (SPEC §9, §16.2).

        The viewer sees the resulting state with hidden enemy units removed and
        only the events they could witness — combat and movement on hexes they can
        see, plus anything involving their own units.
        """
        record = self._repo.get_turn(player.game_id, turn)
        if record is None:
            raise ReplayNotFound(f"game {player.game_id} has no resolved turn {turn}")
        viewer = player.slot
        events = _EVENTS.validate_python(record.events)
        witnessed = filter_events(events, record.state_after, viewer)
        return TurnReplay(
            turn=record.turn,
            events=witnessed,
            state_after=redact_state(record.state_after, viewer),
            victory=record.victory,
        )

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
        self, game: GameRecord, players: list[PlayerRecord]
    ) -> tuple[GameState, str]:
        """Run the engine over the full set of submissions and persist the result.

        Resolution goes through `resolve_plan`, which drops intents that the
        turn's simultaneous dynamics make moot — an order for a unit destroyed
        by pre-movement barrage, or a recruit at a city lost/occupied mid-turn
        (possible under fog even though each submission validated statically).
        The engine treats those stale intents as caller bugs and would crash
        the turn; in a live game they must instead resolve as no-ops.
        """
        turn = game.state.turn
        slot_by_player_id = {p.id: p.slot for p in players}
        submissions = sorted(
            self._repo.list_turn_orders(game.id, turn),
            key=lambda o: slot_by_player_id[o.player_id],
        )

        orders: list[Order] = []
        recruitments: list[Recruitment] = []
        for submission in submissions:
            orders.extend(_ORDERS.validate_python(submission.orders["orders"]))
            recruitments.extend(
                _RECRUITMENTS.validate_python(submission.orders["recruitments"])
            )

        next_state, events = resolve_plan(game.state, orders, recruitments)
        victory = victory_status(next_state)
        status = "finished" if victory.decided else "active"

        self._repo.record_turn(
            game.id,
            turn,
            [event.model_dump(mode="json") for event in events],
            next_state,
            victory,
        )
        self._repo.set_game_state(game.id, next_state, status)
        return next_state, status

    def _require_game(self, game_id: str) -> GameRecord:
        game = self._repo.get_game(game_id)
        if game is None:
            raise GameNotFound(f"no game with id {game_id}")
        return game

    def _require_no_active_game(self, user_id: str) -> None:
        """Enforce one active game per user before a create/join (SPEC: Phase 7)."""
        existing = self._repo.get_active_player_for_user(user_id)
        if existing is not None:
            raise GameAlreadyActive(
                f"user already holds slot {existing.slot!r} in game {existing.game_id}"
            )


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
