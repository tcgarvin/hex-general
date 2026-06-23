"""Tests for the turn-lifecycle service (roadmap Phase 5, accounts in Phase 7).

Drives the lifecycle against the in-memory `FakeGameRepo` and the real engine:
create/join transitions (now keyed on Supabase user ids + invite tokens), the
one-active-game rule, the dispatcher, the both-locked execution trigger, replay
retrieval, the domain error cases, and a full game driven to a turn-limit victory.
"""

from __future__ import annotations

import pytest

from app.games.repo import PlayerRecord
from app.games.service import (
    AlreadySubmitted,
    GameAlreadyActive,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    InvalidInvite,
    ReplayNotFound,
)
from games.fake_repo import FakeGameRepo

BLUE_USER = "user-blue"
RED_USER = "user-red"
OTHER_USER = "user-other"


def _service() -> GameService:
    return GameService(FakeGameRepo())


def _player(service: GameService, game_id: str, user_id: str) -> PlayerRecord:
    """Resolve the stored PlayerRecord for a user in a game."""
    player = service._repo.get_player(game_id, user_id)  # noqa: SLF001
    assert player is not None
    return player


def test_create_starts_in_lobby_with_first_slot() -> None:
    service = _service()
    creds = service.create_game("twin_rivers", BLUE_USER)
    assert creds.slot == "blue"
    assert creds.status == "lobby"
    assert creds.invite_token  # a share link is minted on create


def test_join_activates_game_and_claims_second_slot() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    red = service.join_game(blue.game_id, RED_USER, blue.invite_token)
    assert red.slot == "red"
    assert red.status == "active"


def test_create_with_unknown_scenario_rejected() -> None:
    with pytest.raises(GameNotFound):
        _service().create_game("does_not_exist", BLUE_USER)


def test_join_full_game_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    service.join_game(blue.game_id, RED_USER, blue.invite_token)
    with pytest.raises(GameFull):
        service.join_game(blue.game_id, OTHER_USER, blue.invite_token)


def test_join_with_wrong_invite_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    with pytest.raises(InvalidInvite):
        service.join_game(blue.game_id, RED_USER, "not-the-invite")


def test_create_rejected_when_user_already_active() -> None:
    service = _service()
    service.create_game("twin_rivers", BLUE_USER)
    with pytest.raises(GameAlreadyActive):
        service.create_game("twin_rivers", BLUE_USER)


def test_join_rejected_when_user_already_active() -> None:
    service = _service()
    service.create_game("twin_rivers", BLUE_USER)  # BLUE_USER's active game
    second = service.create_game("twin_rivers", OTHER_USER)
    # BLUE_USER already holds a slot in their own game, so cannot also join `second`.
    with pytest.raises(GameAlreadyActive):
        service.join_game(second.game_id, BLUE_USER, second.invite_token)


def test_dispatch_points_at_active_game() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    dispatch = service.get_dispatch(BLUE_USER)
    assert dispatch.active_game is not None
    assert dispatch.active_game.game_id == blue.game_id
    assert dispatch.active_game.slot == "blue"


def test_dispatch_empty_for_user_without_a_game() -> None:
    assert _service().get_dispatch(BLUE_USER).active_game is None


def test_dispatch_empty_once_game_is_finished() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    red = _player(service, red_creds.game_id, RED_USER)

    turn_limit = service.get_view(blue).state.config.victory.turn_limit
    for _ in range(turn_limit):
        service.submit_orders(blue, [], [])
        service.submit_orders(red, [], [])

    # A finished game no longer counts as the user's active game.
    assert service.get_dispatch(BLUE_USER).active_game is None


def test_submit_before_opponent_joins_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    with pytest.raises(GameNotActive):
        service.submit_orders(_player(service, blue.game_id, BLUE_USER), [], [])


def test_turn_resolves_only_when_both_players_locked() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    red = _player(service, red_creds.game_id, RED_USER)

    first = service.submit_orders(blue, [], [])
    assert first.locked and not first.executed
    assert first.turn == 0

    second = service.submit_orders(red, [], [])
    assert second.locked and second.executed
    assert second.turn == 1  # the engine advanced the turn


def test_double_submit_by_same_player_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    service.join_game(blue.game_id, RED_USER, blue.invite_token)
    player = _player(service, blue.game_id, BLUE_USER)
    service.submit_orders(player, [], [])
    with pytest.raises(AlreadySubmitted):
        service.submit_orders(player, [], [])


def test_view_reports_locked_state() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    service.join_game(blue.game_id, RED_USER, blue.invite_token)
    player = _player(service, blue.game_id, BLUE_USER)
    service.submit_orders(player, [], [])

    view = service.get_view(player)
    assert view.locked == {"blue": True, "red": False}
    assert view.your_slot == "blue"
    assert view.status == "active"
    assert view.invite_token is None  # game is full, nothing to share


def test_view_exposes_invite_while_slot_open() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    view = service.get_view(_player(service, blue.game_id, BLUE_USER))
    assert view.status == "lobby"
    assert view.invite_token == blue.invite_token


def test_replay_available_after_resolution() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    red = _player(service, red_creds.game_id, RED_USER)
    service.submit_orders(blue, [], [])
    service.submit_orders(red, [], [])

    replay = service.get_replay(blue, 0)
    assert replay.turn == 0
    assert replay.state_after.turn == 1


def test_replay_missing_turn_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    service.join_game(blue.game_id, RED_USER, blue.invite_token)
    with pytest.raises(ReplayNotFound):
        service.get_replay(_player(service, blue.game_id, BLUE_USER), 5)


def test_full_game_drives_to_turn_limit_victory() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    red = _player(service, red_creds.game_id, RED_USER)

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


def test_resign_finishes_game_and_awards_opponent() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    red = _player(service, red_creds.game_id, RED_USER)

    view = service.resign(red)
    assert view.status == "finished"
    assert view.victory.reason == "resignation"
    assert view.victory.winner == "blue"
    # The opponent, polling, sees the same decided outcome.
    assert service.get_view(blue).victory.winner == "blue"


def test_resign_in_lobby_rejected() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)
    with pytest.raises(GameNotActive):
        service.resign(_player(service, blue.game_id, BLUE_USER))


def test_resign_after_finish_rejected() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    red = _player(service, red_creds.game_id, RED_USER)
    service.resign(red)
    # Game is already finished; a second resignation has nothing to forfeit.
    blue = _player(service, blue_creds.game_id, BLUE_USER)
    with pytest.raises(GameNotActive):
        service.resign(blue)


def test_history_reports_outcomes_per_user() -> None:
    service = _service()
    blue_creds = service.create_game("twin_rivers", BLUE_USER)
    red_creds = service.join_game(blue_creds.game_id, RED_USER, blue_creds.invite_token)
    red = _player(service, red_creds.game_id, RED_USER)
    service.resign(red)

    blue_history = service.get_history(BLUE_USER)
    assert len(blue_history) == 1
    assert blue_history[0].outcome == "win"
    assert blue_history[0].your_slot == "blue"
    assert blue_history[0].scenario == "twin_rivers"

    red_history = service.get_history(RED_USER)
    assert red_history[0].outcome == "loss"
    assert red_history[0].your_slot == "red"


def test_history_marks_unfinished_games_in_progress() -> None:
    service = _service()
    blue = service.create_game("twin_rivers", BLUE_USER)  # lobby, no opponent yet
    history = service.get_history(BLUE_USER)
    assert history[0].outcome == "in_progress"
    assert history[0].status == "lobby"
    assert blue.game_id == history[0].game_id
