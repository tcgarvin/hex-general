"""The HTTP API over the in-memory repo (WORLD_SPEC §10.1).

Drives the real FastAPI app and routing, with the Supabase repo swapped for the
in-memory fake and JWT verification swapped for a header-driven fake identity (so
each request can act as a chosen user). JWT/dev-impersonation behaviour itself is
unit-tested in test_auth.py against the real dependency.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

import app.games.world_routes as world_routes
import app.main as main_module
from app.engine.economy_rules import Stocks
from app.engine.events import (
    BarrageHit,
    BarrageResolved,
    UnitDisbanded,
    WorldUnitRecruited,
)
from app.engine.map_codec import CompactMap, decode_map
from app.engine.models import GameState, Player
from app.engine.victory import VictoryReason, VictoryStatus
from app.engine.visibility import visible_hexes
from app.games.auth import AuthIdentity
from app.games.deps import current_identity, get_repo, real_identity
from app.games.schemas import DevProfile, WorldState
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
    app.dependency_overrides[real_identity] = _fake_identity
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
    orders = client.post(f"/worlds/{game_id}/orders", json=plan, headers=_as(PLAYER))
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


def test_delete_world_refuses_non_devs(client: TestClient, repo: FakeGameRepo) -> None:
    game_id = _create(client)
    response = client.delete(f"/dev/worlds/{game_id}", headers=_as(PLAYER))
    assert response.status_code == 403
    assert repo.get_game(game_id) is not None


def _join(client: TestClient, game_id: str, user_id: str = PLAYER) -> str:
    response = client.post(f"/worlds/{game_id}/join", headers=_as(user_id))
    assert response.status_code == 201, response.text
    return str(response.json()["slot"])


def test_the_active_world_is_the_one_joined(client: TestClient) -> None:
    game_id = _create(client)
    slot = _join(client, game_id)

    active = client.get("/worlds/active", headers=_as(PLAYER))

    assert active.status_code == 200, active.text
    assert active.json() == {"active_world": {"game_id": game_id, "slot": slot}}


def test_no_active_world_for_a_user_who_joined_none(client: TestClient) -> None:
    active = client.get("/worlds/active", headers=_as("user-nobody"))
    assert active.status_code == 200
    assert active.json() == {"active_world": None}


def test_world_routes_require_auth(client: TestClient) -> None:
    game_id = _create(client)
    assert client.get("/worlds/active").status_code == 401
    assert client.get("/worlds").status_code == 401
    assert client.get(f"/worlds/{game_id}").status_code == 401
    assert client.post(f"/worlds/{game_id}/orders", json={}).status_code == 401


def test_orders_and_replays_need_a_slot_in_the_world(client: TestClient) -> None:
    game_id = _create(client)
    _join(client, game_id)
    client.post(f"/dev/worlds/{game_id}/tick", headers=_as(DEV))
    stranger = _as("user-stranger")
    assert (
        client.post(f"/worlds/{game_id}/orders", json={}, headers=stranger).status_code
        == 403
    )
    assert client.get(f"/worlds/{game_id}/turns/0", headers=stranger).status_code == 403
    assert client.get(f"/worlds/{game_id}/map", headers=stranger).status_code == 403


def test_an_illegal_order_is_a_422(client: TestClient) -> None:
    game_id = _create(client)
    _join(client, game_id)
    response = client.post(
        f"/worlds/{game_id}/orders",
        json={
            "orders": [{"kind": "move", "unit_id": 9999, "path": [{"q": 0, "r": 0}]}]
        },
        headers=_as(PLAYER),
    )
    assert response.status_code == 422
    assert "unknown unit" in response.json()["detail"]


def test_orders_for_a_finished_world_are_a_409(
    client: TestClient, repo: FakeGameRepo
) -> None:
    game_id = _create(client)
    _join(client, game_id)
    game = repo.get_game(game_id)
    assert game is not None
    repo.set_game_state(game, game.state, "finished")

    response = client.post(f"/worlds/{game_id}/orders", json={}, headers=_as(PLAYER))

    assert response.status_code == 409


def test_a_replay_of_an_unresolved_turn_is_a_404(client: TestClient) -> None:
    game_id = _create(client)
    _join(client, game_id)
    response = client.get(f"/worlds/{game_id}/turns/0", headers=_as(PLAYER))
    assert response.status_code == 404


def test_the_view_hides_enemy_units_in_the_fog(
    client: TestClient, repo: FakeGameRepo
) -> None:
    game_id = _create(client, bots=3)
    slot = _join(client, game_id)
    game = repo.get_game(game_id)
    assert game is not None
    visible = visible_hexes(game.state, slot)

    view = client.get(f"/worlds/{game_id}", headers=_as(PLAYER)).json()

    enemies = [u for u in view["state"]["units"] if u["owner"] != slot]
    seen = {(c.q, c.r) for c in visible}
    assert all((u["coord"]["q"], u["coord"]["r"]) in seen for u in enemies)
    total_enemies = sum(1 for u in game.state.units if u.owner != slot)
    assert len(enemies) < total_enemies


def test_a_replay_keeps_other_players_economy_private(client: TestClient) -> None:
    game_id = _create(client, bots=3)
    slot = _join(client, game_id)
    assert (
        client.post(f"/dev/worlds/{game_id}/tick", headers=_as(DEV)).status_code == 200
    )

    replay = client.get(f"/worlds/{game_id}/turns/0", headers=_as(PLAYER)).json()

    accounted = {e["player"] for e in replay["events"] if "player" in e}
    assert accounted <= {slot}
    others = [p for p in replay["state_after"]["players"] if p["id"] != slot]
    assert others
    assert all(p == Player(id=p["id"]).model_dump(mode="json") for p in others)


def test_a_replay_withholds_other_players_private_event_fields(
    client: TestClient, repo: FakeGameRepo
) -> None:
    # Another player recruits, disbands and fires a gun in the fog, all on a
    # hex the caller can see: the replay shows what happened there, never that
    # player's stocks, costs, shortage or hidden gun.
    game_id = _create(client, bots=3)
    slot = _join(client, game_id)
    game = repo.get_game(game_id)
    assert game is not None
    mine = next(u for u in game.state.units if u.owner == slot)
    other = next(u for u in game.state.units if u.owner not in (slot, "neutral"))
    assert other.coord not in visible_hexes(game.state, slot)
    events = [
        WorldUnitRecruited(
            unit_id=999,
            unit_type=other.type,
            owner=other.owner,
            coord=mine.coord,
            cost=Stocks(manpower=3),
            stocks_after=Stocks(manpower=27, fuel=30, steel=10),
        ),
        UnitDisbanded(
            unit_id=998,
            unit_type=other.type,
            owner=other.owner,
            coord=mine.coord,
            shortage="manpower",
            chosen=True,
        ),
        BarrageResolved(
            artillery_id=other.id,
            target=mine.coord,
            hit=BarrageHit(
                unit_id=mine.id,
                unit_type=mine.type,
                owner=slot,
                terrain_bonus=0,
                hold_bonus=0,
                damage=1,
                hp_after=mine.hp - 1,
                destroyed=False,
            ),
        ),
    ]
    repo.record_turn(
        game,
        0,
        [e.model_dump(mode="json") for e in events],
        game.state,
        VictoryStatus(
            reason=VictoryReason.ONGOING, winner=None, eliminated=[], scores={}
        ),
    )

    replay = client.get(f"/worlds/{game_id}/turns/0", headers=_as(PLAYER))

    assert replay.status_code == 200, replay.text
    recruit, disband, barrage = replay.json()["events"]
    assert recruit["unit_id"] == 999
    assert recruit["cost"] is None and recruit["stocks_after"] is None
    assert disband["shortage"] is None and disband["chosen"] is None
    assert barrage["artillery_id"] is None and barrage["hit"]["unit_id"] == mine.id


def test_me_reports_identity_and_dev_role(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "is_dev", lambda user_id: user_id == DEV)
    assert client.get("/me", headers=_as(DEV)).json() == {
        "user_id": DEV,
        "is_dev": True,
    }
    assert client.get("/me", headers=_as(PLAYER)).json() == {
        "user_id": PLAYER,
        "is_dev": False,
    }


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_dev_profiles_lists_self_and_hex_local_accounts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "is_dev", lambda user_id: user_id == DEV)
    monkeypatch.setattr(
        main_module,
        "list_dev_profiles",
        lambda _client, self_user_id: [
            DevProfile(user_id=self_user_id, email="dev@gmail.com", is_self=True),
            DevProfile(user_id="test-red", email="red@hex.local", is_self=False),
        ],
    )
    response = client.get("/dev/profiles", headers=_as(DEV))
    assert response.status_code == 200
    assert response.json() == [
        {"user_id": DEV, "email": "dev@gmail.com", "is_self": True},
        {"user_id": "test-red", "email": "red@hex.local", "is_self": False},
    ]


def test_dev_profiles_forbidden_for_non_devs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "is_dev", lambda user_id: False)
    assert client.get("/dev/profiles", headers=_as(PLAYER)).status_code == 403


def test_dev_profiles_requires_auth(client: TestClient) -> None:
    assert client.get("/dev/profiles").status_code == 401


def test_no_match_routes_remain() -> None:
    assert not any(path.startswith("/games") for path in app.openapi()["paths"])
