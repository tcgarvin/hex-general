"""Turn lifecycle orchestration (roadmap Phase 5).

`GameService` is the application layer between the HTTP routes and the two things
it coordinates: the persistence boundary (`GameRepo`) and the pure execution
engine (`app.engine`). It owns the lifecycle rules — who may join, when a turn
resolves, when a game ends — but holds no I/O itself beyond what the repo does, so
it is unit-testable against an in-memory fake repo.

The central rule: a turn resolves exactly when *every* player in the game has a
locked submission for the current turn. The submission that completes the set
triggers `resolve_execution` synchronously, records the resolved `turns` row, and
advances `games.state`. Victory ends the game (status ``finished``).

Domain failures are raised as typed `GameError` subclasses; the route layer maps
each to an HTTP status. Order validation happens in `validation.py` before a
submission is ever stored, so a stored submission is always individually legal and
a completed set is expected to resolve without the engine raising.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import TypeAdapter

from app.engine.execution import resolve_execution
from app.engine.models import GameState, Order, Recruitment
from app.engine.scenario import load_scenario_file
from app.engine.victory import victory_status
from app.games.identity import generate_player_token
from app.games.repo import GameRecord, GameRepo, PlayerRecord
from app.games.schemas import GameView, PlayerCredentials, SubmitResult, TurnReplay
from app.games.validation import validate_submission

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
_SCENARIO_NAME = re.compile(r"^[a-z0-9_]+$")

_ORDERS = TypeAdapter(list[Order])
_RECRUITMENTS = TypeAdapter(list[Recruitment])


class GameError(Exception):
    """Base class for turn-lifecycle domain failures (each maps to an HTTP code)."""


class GameNotFound(GameError):
    """No game exists with the requested id."""


class GameFull(GameError):
    """Every scenario slot is already claimed; no room to join."""


class GameNotActive(GameError):
    """The game is not accepting orders (still in lobby, or already finished)."""


class AlreadySubmitted(GameError):
    """This player already has a locked submission for the current turn."""


class ReplayNotFound(GameError):
    """No resolved turn with the requested number exists for this game."""


class GameService:
    """Coordinates persistence and the engine across the turn lifecycle."""

    def __init__(self, repo: GameRepo) -> None:
        self._repo = repo

    def create_game(self, scenario: str) -> PlayerCredentials:
        """Create a game from ``scenario`` and claim its first slot for the caller."""
        state = _load_scenario_state(scenario)
        game = self._repo.create_game(scenario, state)
        first_slot = state.players[0].id
        player = self._repo.create_player(game.id, first_slot, generate_player_token())
        return PlayerCredentials(
            game_id=game.id, slot=player.slot, token=player.token, status=game.status
        )

    def join_game(self, game_id: str) -> PlayerCredentials:
        """Claim the next open slot. Activates the game once every slot is filled."""
        game = self._require_game(game_id)
        present = {p.slot for p in self._repo.list_players(game_id)}
        open_slots = [p.id for p in game.state.players if p.id not in present]
        if not open_slots:
            raise GameFull(f"game {game_id} has no open slots")

        slot = open_slots[0]
        player = self._repo.create_player(game_id, slot, generate_player_token())

        status = game.status
        if len(present) + 1 == len(game.state.players):
            status = "active"
            self._repo.set_game_state(game_id, game.state, status)
        return PlayerCredentials(
            game_id=game_id, slot=player.slot, token=player.token, status=status
        )

    def get_view(self, player: PlayerRecord) -> GameView:
        """Return ``player``'s view of their game (full state; fog is Phase 6)."""
        game = self._require_game(player.game_id)
        players = self._repo.list_players(player.game_id)
        slot_by_player_id = {p.id: p.slot for p in players}
        slots_present = sorted(p.slot for p in players)
        submitted = {
            slot_by_player_id[o.player_id]
            for o in self._repo.list_turn_orders(player.game_id, game.state.turn)
        }
        locked = {slot: slot in submitted for slot in slots_present}
        return GameView(
            game_id=game.id,
            status=game.status,
            turn=game.state.turn,
            your_slot=player.slot,
            slots_present=slots_present,
            locked=locked,
            state=game.state,
            victory=victory_status(game.state),
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
        submissions = self._repo.list_turn_orders(game.id, turn)
        if len(submissions) < len(players):
            return SubmitResult(
                locked=True, executed=False, turn=turn, status=game.status
            )

        next_state, status = self._resolve_turn(game, players)
        return SubmitResult(
            locked=True, executed=True, turn=next_state.turn, status=status
        )

    def get_replay(self, player: PlayerRecord, turn: int) -> TurnReplay:
        """Return the resolved replay for ``turn`` of ``player``'s game."""
        record = self._repo.get_turn(player.game_id, turn)
        if record is None:
            raise ReplayNotFound(f"game {player.game_id} has no resolved turn {turn}")
        return TurnReplay(
            turn=record.turn,
            events=record.events,
            state_after=record.state_after,
            victory=record.victory,
        )

    def _resolve_turn(
        self, game: GameRecord, players: list[PlayerRecord]
    ) -> tuple[GameState, str]:
        """Run the engine over the full set of submissions and persist the result."""
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

        next_state, events = resolve_execution(game.state, orders, recruitments)
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
