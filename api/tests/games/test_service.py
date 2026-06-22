"""Tests for the turn-lifecycle service (roadmap Phase 5).

Drives the lifecycle against the in-memory `FakeGameRepo` and the real engine:
create/join transitions, the both-locked execution trigger, replay retrieval, the
domain error cases, and a full game driven to a turn-limit victory.
"""

from __future__ import annotations

import pytest

from app.games.repo import PlayerRecord
from app.games.service import (
    AlreadySubmitted,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    ReplayNotFound,
)
from games.fake_repo import FakeGameRepo


def _service() -> GameService:
    return GameService(FakeGameRepo())


def _player(service: GameService, creds: object) -> PlayerRecord:
    """Resolve a credentials' token back to its stored PlayerRecord."""
    token = creds.token  # type: ignore[attr-defined]
    player = service._repo.get_player_by_token(token)  # noqa: SLF001
    assert player is not None
    return player


def test_create_starts_in_lobby_with_first_slot() -> None:
    service = _service()
    creds = service.create_game("twin_rivers")
    assert creds.slot == "blue"
    assert creds.status == "lobby"


def test_join_activates_game_and_claims_second_slot() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    red = service.join_game(blue.game_id)
    assert red.slot == "red"
    assert red.status == "active"
    assert red.token != blue.token


def test_create_with_unknown_scenario_rejected() -> None:
    with pytest.raises(GameNotFound):
        _service().create_game("does_not_exist")


def test_join_full_game_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    service.join_game(blue.game_id)
    with pytest.raises(GameFull):
        service.join_game(blue.game_id)


def test_submit_before_opponent_joins_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    with pytest.raises(GameNotActive):
        service.submit_orders(_player(service, blue), [], [])


def test_turn_resolves_only_when_both_players_locked() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    red = service.join_game(blue.game_id)

    first = service.submit_orders(_player(service, blue), [], [])
    assert first.locked and not first.executed
    assert first.turn == 0

    second = service.submit_orders(_player(service, red), [], [])
    assert second.locked and second.executed
    assert second.turn == 1  # the engine advanced the turn


def test_double_submit_by_same_player_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    service.join_game(blue.game_id)
    service.submit_orders(_player(service, blue), [], [])
    with pytest.raises(AlreadySubmitted):
        service.submit_orders(_player(service, blue), [], [])


def test_view_reports_locked_state() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    service.join_game(blue.game_id)
    service.submit_orders(_player(service, blue), [], [])

    view = service.get_view(_player(service, blue))
    assert view.locked == {"blue": True, "red": False}
    assert view.your_slot == "blue"
    assert view.status == "active"


def test_replay_available_after_resolution() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    red = service.join_game(blue.game_id)
    service.submit_orders(_player(service, blue), [], [])
    service.submit_orders(_player(service, red), [], [])

    replay = service.get_replay(_player(service, blue), 0)
    assert replay.turn == 0
    assert replay.state_after.turn == 1


def test_replay_missing_turn_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers")
    service.join_game(blue.game_id)
    with pytest.raises(ReplayNotFound):
        service.get_replay(_player(service, blue), 5)


def test_full_game_drives_to_turn_limit_victory() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers")
    red_creds = service.join_game(blue_creds.game_id)
    blue = _player(service, blue_creds)
    red = _player(service, red_creds)

    turn_limit = service.get_view(blue).state.config.victory.turn_limit
    status = "active"
    for _ in range(turn_limit):
        service.submit_orders(blue, [], [])
        status = service.submit_orders(red, [], []).status

    assert status == "finished"
    final = service.get_view(blue)
    assert final.status == "finished"
    assert final.victory.decided
    # Once finished, no further orders are accepted.
    with pytest.raises(GameNotActive):
        service.submit_orders(blue, [], [])
