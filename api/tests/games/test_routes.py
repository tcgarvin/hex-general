"""HTTP integration tests for the turn lifecycle (roadmap Phase 5).

Drives the full create -> join -> submit -> execute -> replay flow through the
real FastAPI app and routing/auth, with the Supabase repo swapped for the
in-memory fake via dependency override. This is the Phase 5 "play a game over the
wire" demonstrable, exercised by two distinct player tokens.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.games.repo import GameRepo
from app.games.routes import get_repo
from app.main import app
from games.fake_repo import FakeGameRepo


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo: GameRepo = FakeGameRepo()
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _auth(token: str) -> dict[str, str]:
    return {"X-Player-Token": token}


def test_full_two_player_game_over_http(client: TestClient) -> None:
    create = client.post("/games", json={"scenario": "twin_rivers"})
    assert create.status_code == 201
    blue = create.json()
    assert blue["slot"] == "blue"
    game_id = blue["game_id"]

    join = client.post(f"/games/{game_id}/join")
    assert join.status_code == 201
    red = join.json()
    assert red["slot"] == "red"
    assert red["status"] == "active"

    view = client.get(f"/games/{game_id}", headers=_auth(blue["token"]))
    assert view.status_code == 200
    assert view.json()["your_slot"] == "blue"
    assert view.json()["locked"] == {"blue": False, "red": False}

    blue_submit = client.post(
        f"/games/{game_id}/orders", json={}, headers=_auth(blue["token"])
    )
    assert blue_submit.status_code == 200
    assert blue_submit.json() == {
        "locked": True,
        "executed": False,
        "turn": 0,
        "status": "active",
    }

    red_submit = client.post(
        f"/games/{game_id}/orders", json={}, headers=_auth(red["token"])
    )
    assert red_submit.status_code == 200
    assert red_submit.json()["executed"] is True
    assert red_submit.json()["turn"] == 1

    replay = client.get(f"/games/{game_id}/turns/0", headers=_auth(blue["token"]))
    assert replay.status_code == 200
    assert replay.json()["turn"] == 0
    assert replay.json()["state_after"]["turn"] == 1


def test_view_requires_token(client: TestClient) -> None:
    create = client.post("/games", json={}).json()
    response = client.get(f"/games/{create['game_id']}")
    assert response.status_code == 422  # missing required header


def test_unknown_token_rejected(client: TestClient) -> None:
    create = client.post("/games", json={}).json()
    response = client.get(
        f"/games/{create['game_id']}", headers=_auth("not-a-real-token")
    )
    assert response.status_code == 401


def test_token_for_other_game_forbidden(client: TestClient) -> None:
    first = client.post("/games", json={}).json()
    second = client.post("/games", json={}).json()
    response = client.get(f"/games/{second['game_id']}", headers=_auth(first["token"]))
    assert response.status_code == 403


def test_unknown_scenario_rejected(client: TestClient) -> None:
    response = client.post("/games", json={"scenario": "nope"})
    assert response.status_code == 404


def test_illegal_order_rejected_with_422(client: TestClient) -> None:
    create = client.post("/games", json={}).json()
    client.post(f"/games/{create['game_id']}/join")
    response = client.post(
        f"/games/{create['game_id']}/orders",
        json={
            "orders": [{"kind": "move", "unit_id": 9999, "path": [{"q": 0, "r": 0}]}]
        },
        headers=_auth(create["token"]),
    )
    assert response.status_code == 422
    assert "unknown unit" in response.json()["detail"]
