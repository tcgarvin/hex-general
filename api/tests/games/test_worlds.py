"""World games through the service: create, join, tick (WORLD_SPEC §10.1)."""

from __future__ import annotations

import pytest

from app.engine.hex_coords import neighbors
from app.engine.models import MoveOrder
from app.games.service import (
    WORLD_BOT_POLICY,
    GameFull,
    GameService,
    NotAWorld,
)
from games.fake_repo import FakeGameRepo


@pytest.fixture
def repo() -> FakeGameRepo:
    return FakeGameRepo()


@pytest.fixture
def service(repo: FakeGameRepo) -> GameService:
    return GameService(repo)


def test_create_world_seats_bots_and_stores_the_map_apart(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    game = repo.get_game(game_id)

    assert game is not None and game.is_world and game.status == "active"
    assert game.state.map is repo.get_map(game.map_id or "")
    assert [p.id for p in game.state.players] == ["s001", "s002"]
    players = repo.list_players(game_id)
    assert {p.slot for p in players} == {"s001", "s002"}
    assert {p.bot_policy for p in players} == {WORLD_BOT_POLICY}


def test_a_human_joins_the_next_open_slot(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    credentials = service.join_world(game_id, "user-1")

    assert credentials.slot == "s003"
    game = repo.get_game(game_id)
    assert game is not None
    assert "s003" in {p.id for p in game.state.players}
    assert any(u.owner == "s003" for u in game.state.units)


def test_joining_a_full_world_is_refused(service: GameService) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=4)
    with pytest.raises(GameFull):
        service.join_world(game_id, "user-1")


def test_a_human_submission_waits_for_the_tick(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None
    game = repo.get_game(game_id)
    assert game is not None
    unit = next(u for u in game.state.units if u.owner == player.slot and u.hp > 0)

    result = service.submit_orders(player, [], [])
    assert not result.executed and result.turn == 0

    tick = service.tick_world(game_id)
    assert tick.executed and tick.turn == 1
    after = repo.get_game(game_id)
    assert after is not None and after.state.turn == 1
    assert after.state.map is game.state.map
    assert any(u.id == unit.id for u in after.state.units)
    assert repo.get_turn(game_id, 0) is not None


def test_ticks_advance_bots_without_any_human(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=4)
    start = repo.get_game(game_id)
    assert start is not None
    for _ in range(3):
        service.tick_world(game_id)
    after = repo.get_game(game_id)
    assert after is not None and after.state.turn == 3
    moved = {u.id: u.coord for u in after.state.units}
    assert any(moved.get(u.id) != u.coord for u in start.state.units)


def test_world_operations_refuse_a_match(service: GameService) -> None:
    credentials = service.create_game("twin_rivers", "user-1")
    with pytest.raises(NotAWorld):
        service.tick_world(credentials.game_id)
    with pytest.raises(NotAWorld):
        service.join_world(credentials.game_id, "user-2")


def test_human_orders_are_resolved_at_the_tick(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    game = repo.get_game(game_id)
    assert player is not None and game is not None
    unit = next(
        u
        for u in game.state.units
        if u.owner == player.slot and game.state.map.index[u.coord].terrain != "city"
    )
    target = next(
        c
        for c in neighbors(unit.coord)
        if c in game.state.map.index
        and game.state.map.index[c].terrain == "plains"
        and game.state.unit_at(c) is None
    )
    service.submit_orders(player, [MoveOrder(unit_id=unit.id, path=[target])], [])
    service.tick_world(game_id)
    after = repo.get_game(game_id)
    assert after is not None
    assert next(u for u in after.state.units if u.id == unit.id).coord == target
