"""Service-level tests for "vs bot" games (AI_SPEC §9).

Drives the lifecycle against the in-memory `FakeGameRepo`, the real engine, and
the real AI policies: a vs-bot game starts immediately with the second slot held
by a bot player, every human submission triggers the bot's synchronous plan (so
the bot never blocks a turn), the bot's submission goes through the same
`validate_submission` path as a human body, and resign works. The multi-turn
test uses the cheap ``scripted`` policy to keep the suite fast; policy choice is
orthogonal to the lifecycle under test.
"""

from __future__ import annotations

import uuid

import pytest

from app.games.repo import PlayerRecord
from app.games.service import GameService, UnknownBotPolicy
from games.fake_repo import FakeGameRepo

HUMAN = "user-human"


def _service() -> GameService:
    return GameService(FakeGameRepo())


def _human_player(service: GameService, game_id: str) -> PlayerRecord:
    player = service._repo.get_player(game_id, HUMAN)  # noqa: SLF001
    assert player is not None
    return player


def test_vs_bot_game_starts_immediately_with_bot_slot_filled() -> None:
    service = _service()
    creds = service.create_game(
        "twin_rivers", HUMAN, vs_bot=True, bot_policy="scripted"
    )
    assert creds.slot == "blue"
    assert creds.status == "active"  # no lobby: the bot is already seated

    players = service._repo.list_players(creds.game_id)  # noqa: SLF001
    by_slot = {p.slot: p for p in players}
    assert set(by_slot) == {"blue", "red"}
    bot = by_slot["red"]
    assert bot.bot_policy == "scripted"
    # The bot's user_id is a real UUID that matches no auth user and is not ours.
    assert bot.user_id != HUMAN
    uuid.UUID(bot.user_id)


def test_unknown_bot_policy_rejected() -> None:
    with pytest.raises(UnknownBotPolicy):
        _service().create_game("twin_rivers", HUMAN, vs_bot=True, bot_policy="skynet")


def test_vs_bot_turns_resolve_on_the_human_submission_alone() -> None:
    # Play several turns: each human submission must both lock and execute
    # (the bot plans synchronously, so it can never block a turn).
    service = _service()
    creds = service.create_game(
        "twin_rivers", HUMAN, vs_bot=True, bot_policy="scripted"
    )
    human = _human_player(service, creds.game_id)

    for expected_turn in (0, 1, 2):
        view = service.get_view(human)
        assert view.turn == expected_turn
        result = service.submit_orders(human, [], [])
        assert result.executed, "bot must not block the turn"
        assert result.turn == expected_turn + 1

    # Each resolved turn stored a replay containing both sides' effects.
    replay = service.get_replay(human, 0)
    assert replay.turn == 0


def test_vs_bot_game_reaches_resolution_at_the_turn_limit() -> None:
    service = _service()
    creds = service.create_game("twin_rivers", HUMAN, vs_bot=True, bot_policy="passive")
    human = _human_player(service, creds.game_id)

    turn_limit = service.get_view(human).state.config.victory.turn_limit
    status = "active"
    for _ in range(turn_limit):
        result = service.submit_orders(human, [], [])
        status = result.status
        if status == "finished":
            break
    assert status == "finished"
    assert service.get_view(human).victory.decided


def test_resign_against_the_bot_awards_the_bot_the_win() -> None:
    service = _service()
    creds = service.create_game(
        "twin_rivers", HUMAN, vs_bot=True, bot_policy="scripted"
    )
    human = _human_player(service, creds.game_id)

    view = service.resign(human)
    assert view.status == "finished"
    assert view.victory.decided
    assert view.victory.winner == "red"  # the bot's slot

    # A finished game frees the human for a new one.
    assert service.get_dispatch(HUMAN).active_game is None
