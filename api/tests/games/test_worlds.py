"""World games through the service: create, join, tick (WORLD_SPEC §10.1)."""

from __future__ import annotations

import gc
from datetime import datetime

from collections.abc import Callable
from typing import Any

import pytest

from app.engine.execution import resolve_execution
from app.engine import hex_coords
from app.engine.hex_coords import Axial, neighbors
from app.engine.economy_rules import Building
from app.engine.models import EconomyOrders
from app.engine.models import NEUTRAL, GameState, MoveOrder, Unit
from app.games.repo import KEEP, GameRecord, Keep
from app.games.service import (
    WORLD_BOT_POLICY,
    GameAlreadyActive,
    GameFull,
    GameNotActive,
    GameNotFound,
    GameService,
    ReplayNotFound,
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

    assert game is not None and game.status == "active"
    assert game.state.map is repo.get_map(game.map_id)
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


def test_the_active_world_is_the_one_the_user_joined(service: GameService) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    credentials = service.join_world(game_id, "user-1")

    active = service.get_active_world("user-1").active_world

    assert active is not None
    assert (active.game_id, active.slot) == (game_id, credentials.slot)


def test_a_user_without_a_world_has_no_active_world(service: GameService) -> None:
    assert service.get_active_world("user-1").active_world is None


def _finish(repo: FakeGameRepo, game_id: str) -> None:
    """End a world outright, as a decided victory would."""
    game = repo.get_game(game_id)
    assert game is not None
    repo.set_game_state(game, game.state, "finished")


def test_a_finished_world_is_no_longer_active_and_refuses_orders_and_ticks(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None

    _finish(repo, game_id)

    assert service.get_active_world("user-1").active_world is None
    with pytest.raises(GameNotActive):
        service.submit_orders(player, [], [])
    with pytest.raises(GameNotActive):
        service.tick_world(game_id)


def test_a_user_in_one_world_cannot_join_another(service: GameService) -> None:
    first = service.create_world(slots=4, seed=3, bots=1)
    second = service.create_world(slots=4, seed=4, bots=1)
    service.join_world(first, "user-1")
    with pytest.raises(GameAlreadyActive):
        service.join_world(second, "user-1")


def test_unknown_worlds_are_not_found(service: GameService) -> None:
    with pytest.raises(GameNotFound):
        service.join_world("no-such-game", "user-1")
    with pytest.raises(GameNotFound):
        service.tick_world("no-such-game")
    with pytest.raises(GameNotFound):
        service.delete_world("no-such-game")


def test_a_replay_of_an_unresolved_turn_is_not_found(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None
    with pytest.raises(ReplayNotFound):
        service.get_world_replay(player, 0)
    service.tick_world(game_id)
    assert service.get_world_replay(player, 0).turn == 0


def test_human_orders_are_resolved_at_the_tick(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    game = repo.get_game(game_id)
    assert player is not None and game is not None
    unit, target = next(
        (u, c)
        for u in game.state.units
        if u.owner == player.slot and game.state.map.index[u.coord].terrain != "city"
        for c in neighbors(u.coord)
        if c in game.state.map.index
        and game.state.map.index[c].terrain == "plains"
        and game.state.unit_at(c) is None
    )
    service.submit_orders(player, [MoveOrder(unit_id=unit.id, path=[target])], [])
    service.tick_world(game_id)
    after = repo.get_game(game_id)
    assert after is not None
    assert next(u for u in after.state.units if u.id == unit.id).coord == target


class _RacingRepo(FakeGameRepo):
    """A fake whose next state write is beaten by ``intrude`` (run just before it).

    Simulates a concurrent writer: the intrusion lands after the service read
    the game but before its compare-and-set.
    """

    def __init__(self) -> None:
        super().__init__()
        self.intrude: Callable[[], object] | None = None

    def set_game_state(
        self,
        game: GameRecord,
        state: GameState,
        status: str,
        *,
        next_tick_at: datetime | None | Keep = KEEP,
    ) -> None:
        intrude, self.intrude = self.intrude, None
        if intrude is not None:
            intrude()
        super().set_game_state(game, state, status, next_tick_at=next_tick_at)


def test_a_join_during_a_tick_survives_the_tick() -> None:
    repo = _RacingRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    repo.intrude = lambda: service.join_world(game_id, "user-1")

    result = service.tick_world(game_id)

    game = repo.get_game(game_id)
    assert game is not None and result.turn == 1 and game.state.turn == 1
    assert "s003" in {p.id for p in game.state.players}
    assert any(u.owner == "s003" for u in game.state.units)
    assert repo.get_turn(game_id, 0) is not None


def test_simultaneous_joiners_get_different_slots() -> None:
    repo = _RacingRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    repo.intrude = lambda: service.join_world(game_id, "user-1")

    late = service.join_world(game_id, "user-2")

    assert late.slot == "s004"
    first = repo.get_player(game_id, "user-1")
    assert first is not None and first.slot == "s003"


def test_a_world_player_can_revise_orders_before_the_tick(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    game = repo.get_game(game_id)
    assert player is not None and game is not None
    state = game.state

    def free_plains(unit: Unit) -> list[Axial]:
        return [
            c
            for c in neighbors(unit.coord)
            if c in state.map.index
            and state.map.index[c].terrain == "plains"
            and state.unit_at(c) is None
        ]

    unit = next(
        u
        for u in state.units
        if u.owner == player.slot
        and state.map.index[u.coord].terrain != "city"
        and len(free_plains(u)) >= 2
    )
    first, second = free_plains(unit)[:2]

    service.submit_orders(player, [MoveOrder(unit_id=unit.id, path=[first])], [])
    service.submit_orders(player, [MoveOrder(unit_id=unit.id, path=[second])], [])
    service.tick_world(game_id)

    after = repo.get_game(game_id)
    assert after is not None
    assert next(u for u in after.state.units if u.id == unit.id).coord == second


def test_a_world_players_build_order_is_stored_and_resolved(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None
    service.submit_orders(
        player, [], [], EconomyOrders(owner=player.slot, build=Building.BARRACKS)
    )
    service.tick_world(game_id)
    after = repo.get_game(game_id)
    assert after is not None
    work = after.state.player(player.slot).construction
    assert work is not None and work.building is Building.BARRACKS


def test_a_world_tick_leaves_the_geometry_caches_empty(
    service: GameService,
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=4)
    hex_coords.line(Axial(q=0, r=0), Axial(q=3, r=-2))
    hex_coords.neighbors(Axial(q=0, r=0))
    hex_coords.within(Axial(q=0, r=0), 2)
    assert hex_coords._line.cache_info().currsize > 0

    service.tick_world(game_id)

    assert hex_coords._neighbors.cache_info().currsize == 0
    assert hex_coords._within.cache_info().currsize == 0
    assert hex_coords._line.cache_info().currsize == 0


def test_a_world_tick_runs_with_the_collector_off_and_restores_it(
    service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)
    seen: list[bool] = []
    real_resolve = resolve_execution

    def resolve(*args: Any) -> Any:
        seen.append(gc.isenabled())
        return real_resolve(*args)

    monkeypatch.setattr("app.games.service.resolve_execution", resolve)
    service.tick_world(game_id)

    assert seen == [False]
    assert gc.isenabled()


def test_a_failed_world_tick_still_restores_the_collector(
    service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2)

    def resolve(*_: Any) -> Any:
        raise RuntimeError("engine failure")

    monkeypatch.setattr("app.games.service.resolve_execution", resolve)
    with pytest.raises(RuntimeError, match="engine failure"):
        service.tick_world(game_id)

    assert gc.isenabled()


class _FailingInsertRepo(FakeGameRepo):
    """A fake whose next players insert runs ``before`` and then ``outcome``.

    ``before`` is a concurrent request landing between the seat and the insert;
    ``outcome`` decides how the insert ends: ``"raise"`` fails it without
    writing the row (a transient DB error), ``"lost"`` writes the row and then
    raises (the response was lost after the insert committed), and
    ``"insert"`` runs it normally (it may still fail on a unique constraint).
    """

    def __init__(self) -> None:
        super().__init__()
        self.before: Callable[[], object] | None = None
        self.outcome: str | None = None

    def create_player(
        self, game_id: str, slot: str, user_id: str, bot_policy: str | None = None
    ) -> Any:
        before, self.before = self.before, None
        outcome, self.outcome = self.outcome, None
        if before is not None:
            before()
        if outcome == "raise":
            raise ConnectionError("database unavailable")
        player = super().create_player(game_id, slot, user_id, bot_policy)
        if outcome == "lost":
            raise ConnectionError("response lost after the insert")
        return player


def _seated(repo: FakeGameRepo, game_id: str) -> set[str]:
    game = repo.get_game(game_id)
    assert game is not None
    return {p.id for p in game.state.players}


def _assert_no_ghost_slot(repo: FakeGameRepo, game_id: str) -> None:
    """Every player seated in the state has a players row, and vice versa."""
    game = repo.get_game(game_id)
    assert game is not None
    rows = {p.slot for p in repo.list_players(game_id)}
    assert _seated(repo, game_id) == rows
    owners = {u.owner for u in game.state.units if u.owner != NEUTRAL}
    assert owners <= rows


def test_a_join_whose_player_insert_fails_takes_its_seat_back() -> None:
    repo = _FailingInsertRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    before = repo.get_game(game_id)
    assert before is not None
    repo.outcome = "raise"

    with pytest.raises(ConnectionError, match="database unavailable"):
        service.join_world(game_id, "user-1")

    _assert_no_ghost_slot(repo, game_id)
    after = repo.get_game(game_id)
    assert after is not None
    assert after.state.settlements == before.state.settlements
    assert after.state.units == before.state.units
    assert service.join_world(game_id, "user-1").slot == "s003"


def test_a_join_whose_insert_landed_before_the_error_stands() -> None:
    repo = _FailingInsertRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    repo.outcome = "lost"

    credentials = service.join_world(game_id, "user-1")

    assert credentials.slot == "s003"
    _assert_no_ghost_slot(repo, game_id)


def test_the_same_user_joining_twice_at_once_holds_one_slot() -> None:
    repo = _FailingInsertRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    # The second click's request lands in full between the first one's seat
    # and its insert; both passed the one-active-game check before either row.
    repo.before = lambda: service.join_world(game_id, "user-1")

    with pytest.raises(GameAlreadyActive):
        service.join_world(game_id, "user-1")

    _assert_no_ghost_slot(repo, game_id)
    rows = [p for p in repo.list_players(game_id) if p.user_id == "user-1"]
    assert [p.slot for p in rows] == ["s004"]
    assert service.join_world(game_id, "user-2").slot == "s003"


def test_two_users_racing_for_one_slot_each_get_their_own() -> None:
    repo = _FailingInsertRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    repo.before = lambda: service.join_world(game_id, "user-2")

    first = service.join_world(game_id, "user-1")

    assert first.slot == "s003"
    second = repo.get_player(game_id, "user-2")
    assert second is not None and second.slot == "s004"
    _assert_no_ghost_slot(repo, game_id)


def test_a_failed_join_is_undone_even_after_a_tick_landed_in_between() -> None:
    repo = _FailingInsertRepo()
    service = GameService(repo)
    game_id = service.create_world(slots=4, seed=3, bots=2)
    repo.before = lambda: service.tick_world(game_id)
    repo.outcome = "raise"

    with pytest.raises(ConnectionError):
        service.join_world(game_id, "user-1")

    game = repo.get_game(game_id)
    assert game is not None and game.state.turn == 1
    _assert_no_ghost_slot(repo, game_id)
