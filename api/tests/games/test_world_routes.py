"""World routes over HTTP (WORLD_SPEC §10.1), against the in-memory repo."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

import app.games.world_routes as world_routes
from app.engine.map_codec import CompactMap, decode_map
from app.engine.models import GameState
from app.games.auth import AuthIdentity
from app.games.routes import current_identity, get_repo
from app.games.schemas import WorldState
from app.main import app
from games.fake_repo import FakeGameRepo

DEV = "user-dev"
PLAYER = "user-player"


def _fake_identity(x_test_user: str | None = Header(default=None)) -> AuthIdentity:
    if x_test_user is None:
        raise HTTPException(status_code=401, detail="missing test user")
    return AuthIdentity(user_id=x_test_user)


@pytest.fixture
def repo() -> FakeGameRepo:
    return FakeGameRepo()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, repo: FakeGameRepo) -> Iterator[TestClient]:
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_identity] = _fake_identity
    monkeypatch.setattr(world_routes, "is_dev", lambda user_id: user_id == DEV)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user_id: str) -> dict[str, str]:
    return {"X-Test-User": user_id}


def _create(client: TestClient, bots: int = 2) -> str:
    response = client.post(
        "/dev/worlds", json={"slots": 4, "seed": 3, "bots": bots}, headers=_as(DEV)
    )
    assert response.status_code == 201, response.text
    return str(response.json()["game_id"])


def test_world_state_mirrors_game_state_without_the_map() -> None:
    assert set(WorldState.model_fields) == set(GameState.model_fields) - {"map"}


def test_join_view_map_tick_and_replay(client: TestClient) -> None:
    game_id = _create(client)

    join = client.post(f"/worlds/{game_id}/join", headers=_as(PLAYER))
    assert join.status_code == 201, join.text
    slot = join.json()["slot"]

    view = client.get(f"/worlds/{game_id}", headers=_as(PLAYER))
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["your_slot"] == slot and body["turn"] == 0
    assert body["submission"] is None
    assert "map" not in body["state"]

    game_map = client.get(f"/worlds/{game_id}/map", headers=_as(PLAYER))
    assert game_map.status_code == 200
    assert "immutable" in game_map.headers["cache-control"]
    decoded = decode_map(CompactMap.model_validate(game_map.json()))
    assert decoded.width == 49

    unit = next(u for u in body["state"]["units"] if u["owner"] == slot)
    plan = {
        "orders": [{"kind": "hold", "unit_id": unit["id"]}],
        "recruitments": [],
        "build": "barracks",
        "disband": [unit["id"]],
    }
    orders = client.post(f"/games/{game_id}/orders", json=plan, headers=_as(PLAYER))
    assert orders.status_code == 200 and orders.json()["executed"] is False
    after = client.get(f"/worlds/{game_id}", headers=_as(PLAYER)).json()
    assert after["submission"] == plan

    tick = client.post(f"/dev/worlds/{game_id}/tick", headers=_as(DEV))
    assert tick.status_code == 200 and tick.json()["turn"] == 1

    replay = client.get(f"/worlds/{game_id}/turns/0", headers=_as(PLAYER))
    assert replay.status_code == 200, replay.text
    assert "map" not in replay.json()["state_after"]


def test_dev_endpoints_refuse_non_devs(client: TestClient) -> None:
    create = client.post(
        "/dev/worlds", json={"slots": 4, "bots": 1}, headers=_as(PLAYER)
    )
    assert create.status_code == 403
    game_id = _create(client)
    assert (
        client.post(f"/dev/worlds/{game_id}/tick", headers=_as(PLAYER)).status_code
        == 403
    )


def test_world_views_need_a_slot_and_a_full_world_refuses_joins(
    client: TestClient,
) -> None:
    game_id = _create(client, bots=4)
    assert client.get(f"/worlds/{game_id}", headers=_as(PLAYER)).status_code == 403
    assert client.post(f"/worlds/{game_id}/join", headers=_as(PLAYER)).status_code == (
        409
    )


def test_too_many_bots_is_a_422(client: TestClient) -> None:
    response = client.post(
        "/dev/worlds", json={"slots": 4, "bots": 5}, headers=_as(DEV)
    )
    assert response.status_code == 422


def test_delete_world_removes_game_players_turns_orders_and_map(
    client: TestClient, repo: FakeGameRepo
) -> None:
    game_id = _create(client)
    map_id = repo.get_game(game_id).map_id  # type: ignore[union-attr]
    assert (
        client.post(f"/worlds/{game_id}/join", headers=_as(PLAYER)).status_code == 201
    )
    assert (
        client.post(f"/dev/worlds/{game_id}/tick", headers=_as(DEV)).status_code == 200
    )
    assert repo.list_players(game_id) and repo.get_turn(game_id, 0) is not None

    response = client.delete(f"/dev/worlds/{game_id}", headers=_as(DEV))

    assert response.status_code == 204
    assert repo.get_game(game_id) is None
    assert repo.list_players(game_id) == []
    assert repo.get_turn(game_id, 0) is None
    assert repo.list_turn_orders(game_id, 0) == []
    assert map_id not in repo._maps
    assert repo.get_active_player_for_user(PLAYER) is None


def test_deleting_a_world_frees_the_account_to_join_another(
    client: TestClient,
) -> None:
    first = _create(client)
    second = _create(client)
    assert client.post(f"/worlds/{first}/join", headers=_as(PLAYER)).status_code == 201
    assert client.post(f"/worlds/{second}/join", headers=_as(PLAYER)).status_code == 409

    assert client.delete(f"/dev/worlds/{first}", headers=_as(DEV)).status_code == 204

    assert client.post(f"/worlds/{second}/join", headers=_as(PLAYER)).status_code == 201


def test_delete_world_unknown_id_is_404(client: TestClient) -> None:
    response = client.delete("/dev/worlds/no-such-game", headers=_as(DEV))
    assert response.status_code == 404


def test_delete_world_refuses_a_two_player_match(
    client: TestClient, repo: FakeGameRepo
) -> None:
    created = client.post("/games", json={"vs_bot": True}, headers=_as(PLAYER))
    assert created.status_code == 201, created.text
    game_id = created.json()["game_id"]

    response = client.delete(f"/dev/worlds/{game_id}", headers=_as(DEV))

    assert response.status_code == 409
    assert "not a world" in response.json()["detail"]
    assert repo.get_game(game_id) is not None


def test_delete_world_refuses_non_devs(client: TestClient, repo: FakeGameRepo) -> None:
    game_id = _create(client)
    response = client.delete(f"/dev/worlds/{game_id}", headers=_as(PLAYER))
    assert response.status_code == 403
    assert repo.get_game(game_id) is not None
