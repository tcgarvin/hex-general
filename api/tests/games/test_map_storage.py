"""World games store their map once (WORLD_SPEC §10.1)."""

from __future__ import annotations

import pytest

from app.engine.models import GameMap
from app.engine.victory import victory_status
from app.games.repo import MapCache, RepoError, join_state, state_blob
from app.world.generate import GeneratedWorld, WorldParams, generate
from games.fake_repo import FakeGameRepo


@pytest.fixture(scope="module")
def world() -> GeneratedWorld:
    return generate(WorldParams(slots=4, width=48, height=48, seed=7))


def test_state_blob_omits_the_map_only_when_it_is_stored_apart(
    world: GeneratedWorld,
) -> None:
    state = world.to_state(["a", "b"])
    assert "map" in state_blob(state, None)
    blob = state_blob(state, "map-1")
    assert "map" not in blob
    assert blob["units"] == state.model_dump(mode="json")["units"]


def test_join_state_reuses_the_given_map_instance(world: GeneratedWorld) -> None:
    state = world.to_state(["a", "b"])
    joined = join_state(state_blob(state, "map-1"), world.game_map)
    assert joined.map is world.game_map
    assert joined.model_dump(mode="json") == state.model_dump(mode="json")


def test_join_state_without_a_map_parses_the_embedded_one(
    world: GeneratedWorld,
) -> None:
    state = world.to_state(["a", "b"])
    joined = join_state(state_blob(state, None), None)
    assert joined.map.hexes == state.map.hexes


def test_join_state_rejects_an_embedded_map_for_a_game_with_a_map_row(
    world: GeneratedWorld,
) -> None:
    state = world.to_state(["a", "b"])
    with pytest.raises(RepoError, match="embedded map"):
        join_state(state_blob(state, None), world.game_map)


def test_map_cache_evicts_the_least_recently_used() -> None:
    cache = MapCache(capacity=2)
    maps = {key: GameMap(width=1, height=1, hexes=[]) for key in "abc"}
    cache.put("a", maps["a"])
    cache.put("b", maps["b"])
    assert cache.get("a") is maps["a"]
    cache.put("c", maps["c"])
    assert cache.get("b") is None
    assert cache.get("a") is maps["a"]
    assert cache.get("c") is maps["c"]


def test_world_states_and_turns_share_the_stored_map(world: GeneratedWorld) -> None:
    repo = FakeGameRepo()
    map_id = repo.create_map(world.game_map, {"seed": 7})
    stored_map = repo.get_map(map_id)
    state = world.to_state(["a", "b"])

    game = repo.create_game("world", state, "token", map_id=map_id)
    assert game.map_id == map_id
    assert game.state.map is stored_map

    repo.set_game_state(game, game.state, "active")
    reloaded = repo.get_game(game.id)
    assert reloaded is not None and reloaded.state.map is stored_map

    repo.record_turn(game, 0, [], game.state, victory_status(game.state))
    turn = repo.get_turn(game.id, 0)
    assert turn is not None and turn.state_after.map is stored_map
