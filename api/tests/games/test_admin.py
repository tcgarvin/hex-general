"""The admin role: world management for devs and the ``ADMIN_USER_IDS`` allowlist.

Drives the real app with the real identity dependencies (`real_identity`,
`current_identity`, `require_admin`); only JWT verification is faked (a bearer
token is taken to be the user id) and the settings carry chosen allowlists, so
`is_dev` and `is_admin` run as deployed. Covers who may reach the admin routes,
that the admin allowlist grants no impersonation, the admin list payload, the
allowlist parsing, and the CORS preflight for the admin screen's DELETE.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.games import auth, deps
from app.games.auth import AuthIdentity
from app.games.deps import get_repo
from app.main import app
from games.fake_repo import FakeGameRepo

DEV = "user-dev"
ADMIN = "user-admin"
PLAYER = "user-player"


def _settings(dev_user_ids: str = "", admin_user_ids: str = "") -> Settings:
    # _env_file=None keeps the test hermetic: a local api/.env sets DEV_USER_IDS.
    return Settings(
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="k",
        dev_user_ids=dev_user_ids,
        admin_user_ids=admin_user_ids,
        _env_file=None,
    )


@pytest.fixture
def repo() -> FakeGameRepo:
    return FakeGameRepo()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, repo: FakeGameRepo) -> Iterator[TestClient]:
    settings = _settings(dev_user_ids=DEV, admin_user_ids=f" {ADMIN} ,")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "verify_jwt", lambda token: AuthIdentity(user_id=token))
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user_id: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}", **extra}


def _create(client: TestClient, user_id: str = ADMIN, **body: object) -> str:
    payload = {"slots": 4, "seed": 3, "bots": 2, **body}
    response = client.post("/dev/worlds", json=payload, headers=_as(user_id))
    assert response.status_code == 201, response.text
    return str(response.json()["game_id"])


@pytest.mark.parametrize("user_id", [ADMIN, DEV])
def test_admins_and_devs_may_manage_worlds(client: TestClient, user_id: str) -> None:
    game_id = _create(client, user_id)

    listed = client.get("/dev/worlds", headers=_as(user_id))
    tick = client.post(f"/dev/worlds/{game_id}/tick", headers=_as(user_id))
    sweep = client.post("/dev/clock", headers=_as(user_id))
    delete = client.delete(f"/dev/worlds/{game_id}", headers=_as(user_id))

    assert listed.status_code == 200
    assert tick.status_code == 200 and tick.json()["turn"] == 1
    assert sweep.status_code == 200
    assert delete.status_code == 204


def test_an_ordinary_user_is_refused_every_admin_route(
    client: TestClient, repo: FakeGameRepo
) -> None:
    game_id = _create(client)
    calls = [
        ("GET", "/dev/worlds"),
        ("POST", "/dev/worlds"),
        ("POST", f"/dev/worlds/{game_id}/tick"),
        ("POST", "/dev/clock"),
        ("DELETE", f"/dev/worlds/{game_id}"),
    ]
    for method, path in calls:
        body = {"slots": 4, "bots": 1} if path == "/dev/worlds" else None
        response = client.request(method, path, json=body, headers=_as(PLAYER))
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"] == "admin-only endpoint"
    game = repo.get_game(game_id)
    assert game is not None and game.state.turn == 0


def test_anonymous_callers_get_a_401(client: TestClient) -> None:
    assert client.get("/dev/worlds").status_code == 401
    assert client.post("/dev/worlds", json={"slots": 4, "bots": 1}).status_code == 401


def test_an_admin_who_is_not_a_dev_cannot_impersonate(client: TestClient) -> None:
    game_id = _create(client)
    as_player = _as(ADMIN, **{"X-Dev-Profile": PLAYER})

    on_admin_route = client.get("/dev/worlds", headers=as_player)
    on_player_route = client.get(f"/worlds/{game_id}", headers=as_player)
    profiles = client.get("/dev/profiles", headers=_as(ADMIN))

    for response in (on_admin_route, on_player_route):
        assert response.status_code == 403
        assert response.json()["detail"] == "not authorized to impersonate users"
    assert profiles.status_code == 403


def test_a_dev_acting_as_a_player_keeps_admin_rights(client: TestClient) -> None:
    # The role is the real account's, so the switcher does not lock a dev out.
    response = client.get("/dev/worlds", headers=_as(DEV, **{"X-Dev-Profile": PLAYER}))
    assert response.status_code == 200


def test_me_reports_the_admin_role(client: TestClient) -> None:
    assert client.get("/me", headers=_as(ADMIN)).json() == {
        "user_id": ADMIN,
        "is_dev": False,
        "is_admin": True,
    }
    assert client.get("/me", headers=_as(DEV)).json()["is_admin"] is True
    assert client.get("/me", headers=_as(PLAYER)).json()["is_admin"] is False


def test_admin_list_has_every_world_with_its_seats_newest_first(
    client: TestClient,
) -> None:
    older = _create(client, slots=4, bots=1, clock="manual")
    newer = _create(client, slots=4, bots=2, seed=11, clock="twice_daily")
    joined = client.post(f"/worlds/{older}/join", headers=_as(PLAYER))
    assert joined.status_code == 201

    body = client.get("/dev/worlds", headers=_as(ADMIN)).json()

    assert body["schedule"] == {"zone": "America/New_York", "hours": [1, 13]}
    first, second = body["worlds"]
    assert first["game_id"] == newer and second["game_id"] == older
    assert {k: first[k] for k in ("status", "turn", "slots", "humans", "bots")} == {
        "status": "active",
        "turn": 0,
        "slots": 4,
        "humans": 0,
        "bots": 2,
    }
    assert first["open"] == 2 and first["seed"] == 11
    assert first["clock"] == "twice_daily" and first["next_tick_at"] is not None
    assert first["width"] == first["height"] > 0
    assert (second["humans"], second["bots"], second["open"]) == (1, 1, 2)
    assert second["clock"] == "manual" and second["next_tick_at"] is None
    assert second["created_at"] < first["created_at"]


def test_more_bots_than_slots_is_refused_before_generating(
    client: TestClient,
) -> None:
    response = client.post(
        "/dev/worlds", json={"slots": 4, "bots": 5}, headers=_as(ADMIN)
    )
    assert response.status_code == 422
    assert "cannot exceed slots" in response.text


def test_cors_preflight_allows_delete_from_the_site() -> None:
    response = TestClient(app).options(
        "/dev/worlds/some-world",
        headers={
            "Origin": "https://hex-general.vercel.app",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]
    assert (
        response.headers["access-control-allow-origin"]
        == "https://hex-general.vercel.app"
    )


def test_cors_preflight_still_refuses_other_origins() -> None:
    response = TestClient(app).options(
        "/dev/worlds/some-world",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_admin_user_id_set_parses_csv() -> None:
    settings = _settings(admin_user_ids=" a , b ,, c ")
    assert settings.admin_user_id_set == frozenset({"a", "b", "c"})


def test_admin_user_id_set_empty_by_default() -> None:
    settings = Settings(
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="k",
        _env_file=None,
    )
    assert settings.admin_user_id_set == frozenset()


def test_is_admin_covers_devs_and_the_allowlist_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth, "get_settings", lambda: _settings(dev_user_ids=DEV, admin_user_ids=ADMIN)
    )
    assert auth.is_admin(DEV) and auth.is_admin(ADMIN)
    assert not auth.is_admin(PLAYER)
    assert not auth.is_dev(ADMIN)


def test_an_empty_admin_list_leaves_only_devs_as_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(dev_user_ids=DEV))
    assert auth.is_admin(DEV)
    assert not auth.is_admin(ADMIN)
