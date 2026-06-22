"""HTTP integration tests for the turn lifecycle (Phase 5, accounts in Phase 7).

Drives the full create -> join -> submit -> execute -> replay flow through the real
FastAPI app and routing, with the Supabase repo swapped for the in-memory fake and
JWT verification swapped for a header-driven fake identity (so each request can act
as a chosen user). JWT/dev-impersonation behavior itself is unit-tested in
test_auth.py against the real dependency.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

from app.games.auth import AuthIdentity
from app.games.repo import GameRepo
from app.games.routes import current_identity, get_repo
from app.main import app
from games.fake_repo import FakeGameRepo

BLUE_USER = "user-blue"
RED_USER = "user-red"


def _fake_identity(x_test_user: str | None = Header(default=None)) -> AuthIdentity:
    """Stand-in for `current_identity`: the X-Test-User header is the caller."""
    if x_test_user is None:
        raise HTTPException(status_code=401, detail="missing test user")
    return AuthIdentity(user_id=x_test_user)


@pytest.fixture
def client() -> Iterator[TestClient]:
    repo: GameRepo = FakeGameRepo()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_identity] = _fake_identity
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user_id: str) -> dict[str, str]:
    return {"X-Test-User": user_id}


def test_full_two_player_game_over_http(client: TestClient) -> None:
    create = client.post(
        "/games", json={"scenario": "twin_rivers"}, headers=_as(BLUE_USER)
    )
    assert create.status_code == 201
    blue = create.json()
    assert blue["slot"] == "blue"
    game_id = blue["game_id"]

    join = client.post(
        f"/games/{game_id}/join",
        json={"invite_token": blue["invite_token"]},
        headers=_as(RED_USER),
    )
    assert join.status_code == 201
    red = join.json()
    assert red["slot"] == "red"
    assert red["status"] == "active"

    view = client.get(f"/games/{game_id}", headers=_as(BLUE_USER))
    assert view.status_code == 200
    assert view.json()["your_slot"] == "blue"
    assert view.json()["locked"] == {"blue": False, "red": False}

    blue_submit = client.post(
        f"/games/{game_id}/orders", json={}, headers=_as(BLUE_USER)
    )
    assert blue_submit.status_code == 200
    assert blue_submit.json() == {
        "locked": True,
        "executed": False,
        "turn": 0,
        "status": "active",
    }

    red_submit = client.post(f"/games/{game_id}/orders", json={}, headers=_as(RED_USER))
    assert red_submit.status_code == 200
    assert red_submit.json()["executed"] is True
    assert red_submit.json()["turn"] == 1

    replay = client.get(f"/games/{game_id}/turns/0", headers=_as(BLUE_USER))
    assert replay.status_code == 200
    assert replay.json()["turn"] == 0
    assert replay.json()["state_after"]["turn"] == 1


def _new_active_game(client: TestClient) -> str:
    """Create + fill a twin_rivers game; return its id."""
    create = client.post(
        "/games", json={"scenario": "twin_rivers"}, headers=_as(BLUE_USER)
    ).json()
    client.post(
        f"/games/{create['game_id']}/join",
        json={"invite_token": create["invite_token"]},
        headers=_as(RED_USER),
    )
    return str(create["game_id"])


def test_view_hides_enemy_units_in_the_fog(client: TestClient) -> None:
    # twin_rivers starts the two sides far apart, so neither player's view should
    # ever contain one of the other's units (roadmap P6 demonstrable).
    game_id = _new_active_game(client)

    blue_view = client.get(f"/games/{game_id}", headers=_as(BLUE_USER)).json()
    assert {u["owner"] for u in blue_view["state"]["units"]} == {"blue"}

    red_view = client.get(f"/games/{game_id}", headers=_as(RED_USER)).json()
    assert {u["owner"] for u in red_view["state"]["units"]} == {"red"}


def test_replay_hides_enemy_units_and_keeps_economy_private(client: TestClient) -> None:
    game_id = _new_active_game(client)
    client.post(f"/games/{game_id}/orders", json={}, headers=_as(BLUE_USER))
    client.post(f"/games/{game_id}/orders", json={}, headers=_as(RED_USER))

    replay = client.get(f"/games/{game_id}/turns/0", headers=_as(BLUE_USER)).json()
    assert {u["owner"] for u in replay["state_after"]["units"]} == {"blue"}
    economy_players = {
        e["player"] for e in replay["events"] if e["kind"] == "economy_resolved"
    }
    assert economy_players <= {"blue"}  # the enemy's economy never leaks


def test_dispatcher_reports_active_game(client: TestClient) -> None:
    game_id = _new_active_game(client)
    dispatch = client.get("/games/active", headers=_as(BLUE_USER)).json()
    assert dispatch["active_game"]["game_id"] == game_id
    assert dispatch["active_game"]["slot"] == "blue"


def test_dispatcher_empty_for_user_without_a_game(client: TestClient) -> None:
    dispatch = client.get("/games/active", headers=_as("user-nobody")).json()
    assert dispatch["active_game"] is None


def test_non_member_forbidden(client: TestClient) -> None:
    game_id = _new_active_game(client)
    response = client.get(f"/games/{game_id}", headers=_as("user-stranger"))
    assert response.status_code == 403


def test_view_requires_auth(client: TestClient) -> None:
    game_id = _new_active_game(client)
    response = client.get(f"/games/{game_id}")  # no X-Test-User
    assert response.status_code == 401


def test_join_with_wrong_invite_forbidden(client: TestClient) -> None:
    create = client.post("/games", json={}, headers=_as(BLUE_USER)).json()
    response = client.post(
        f"/games/{create['game_id']}/join",
        json={"invite_token": "wrong"},
        headers=_as(RED_USER),
    )
    assert response.status_code == 403


def test_create_when_already_active_conflict(client: TestClient) -> None:
    client.post("/games", json={}, headers=_as(BLUE_USER))
    response = client.post("/games", json={}, headers=_as(BLUE_USER))
    assert response.status_code == 409


def test_unknown_scenario_rejected(client: TestClient) -> None:
    response = client.post("/games", json={"scenario": "nope"}, headers=_as(BLUE_USER))
    assert response.status_code == 404


def test_illegal_order_rejected_with_422(client: TestClient) -> None:
    game_id = _new_active_game(client)
    response = client.post(
        f"/games/{game_id}/orders",
        json={
            "orders": [{"kind": "move", "unit_id": 9999, "path": [{"q": 0, "r": 0}]}]
        },
        headers=_as(BLUE_USER),
    )
    assert response.status_code == 422
    assert "unknown unit" in response.json()["detail"]
