"""Scheduled worlds: due sweeps, repeat safety, failure isolation, the clock route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.games import world_routes
from app.games.routes import get_service
from app.games.scheduler_auth import (
    ClockNotConfigured,
    SchedulerAuthError,
    verify_scheduler_token,
)
from app.games.service import GameService, TurnClosed, WorldTicksFailed
from app.main import app
from games.fake_repo import FakeGameRepo

UTC = timezone.utc
START = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)  # 10:00 New York


class Clock:
    """A settable 'now' for the service."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def repo() -> FakeGameRepo:
    return FakeGameRepo()


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def service(repo: FakeGameRepo, clock: Clock) -> GameService:
    return GameService(repo, now=clock)


def test_a_scheduled_world_waits_for_its_tick(
    repo: FakeGameRepo, service: GameService, clock: Clock
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2, clock="twice_daily")
    game = repo.get_game(game_id)
    assert game is not None
    assert game.next_tick_at == datetime(2026, 11, 10, 18, tzinfo=UTC)  # 13:00 NY

    assert service.tick_due_worlds().ticked == []
    clock.now = datetime(2026, 11, 10, 18, 0, 30, tzinfo=UTC)
    assert service.tick_due_worlds().ticked == [game_id]
    after = repo.get_game(game_id)
    assert after is not None and after.state.turn == 1
    assert after.next_tick_at == datetime(2026, 11, 11, 6, tzinfo=UTC)  # 01:00 NY

    # The scheduler retrying, or firing twice, resolves nothing more.
    assert service.tick_due_worlds().ticked == []
    assert (repo.get_game(game_id) or after).state.turn == 1


def test_a_late_clock_resolves_one_turn_and_reschedules_from_now(
    repo: FakeGameRepo, service: GameService, clock: Clock
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1, clock="twice_daily")
    clock.now = START + timedelta(days=2)  # missed several ticks
    service.tick_due_worlds()
    game = repo.get_game(game_id)
    assert game is not None and game.state.turn == 1
    assert game.next_tick_at is not None and game.next_tick_at > clock.now


def test_manual_worlds_and_manual_ticks_leave_schedules_alone(
    repo: FakeGameRepo, service: GameService, clock: Clock
) -> None:
    manual = service.create_world(slots=4, seed=3, bots=1)
    scheduled = service.create_world(slots=4, seed=4, bots=1, clock="twice_daily")
    service.tick_world(scheduled)  # a dev tick between scheduled ones
    game = repo.get_game(scheduled)
    assert game is not None and game.next_tick_at == datetime(
        2026, 11, 10, 18, tzinfo=UTC
    )
    clock.now = START + timedelta(days=1)
    run = service.tick_due_worlds()
    assert run.ticked == [scheduled]
    assert (repo.get_game(manual) or game).state.turn == 0


def test_one_failing_world_does_not_stop_the_others(
    repo: FakeGameRepo,
    service: GameService,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = service.create_world(slots=4, seed=3, bots=1, clock="twice_daily")
    bad = service.create_world(slots=4, seed=4, bots=1, clock="twice_daily")
    original = service._tick_game

    def flaky(game, *, next_tick_at):  # type: ignore[no-untyped-def]
        if game.id == bad:
            raise RuntimeError("boom")
        return original(game, next_tick_at=next_tick_at)

    monkeypatch.setattr(service, "_tick_game", flaky)
    clock.now = START + timedelta(days=1)
    with pytest.raises(WorldTicksFailed) as failure:
        service.tick_due_worlds()
    assert list(failure.value.failed) == [bad]
    good_game = repo.get_game(good)
    assert good_game is not None and good_game.state.turn == 1


# --- scheduler token ----------------------------------------------------------


class _Key:
    def __init__(self, key: object) -> None:
        self.key = key


class _Jwks:
    """Stands in for Google's JWKS: always returns our test public key."""

    def __init__(self, public: object) -> None:
        self.public = public

    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return _Key(self.public)


@pytest.fixture(scope="module")
def keys() -> tuple[rsa.RSAPrivateKey, _Jwks]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, _Jwks(private.public_key())


AUD = "https://hex-api.example"
INVOKER = "clock@hex-general.iam.gserviceaccount.com"


def _token(private: rsa.RSAPrivateKey, **overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": AUD,
        "email": INVOKER,
        "email_verified": True,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256")


def test_a_google_token_for_the_invoker_passes(
    keys: tuple[rsa.RSAPrivateKey, _Jwks],
) -> None:
    private, jwks = keys
    verify_scheduler_token(_token(private), AUD, INVOKER, jwks)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://elsewhere"},
        {"iss": "https://evil.example"},
        {"email": "someone@example.com"},
        {"email_verified": False},
        {"exp": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
)
def test_other_tokens_are_refused(
    keys: tuple[rsa.RSAPrivateKey, _Jwks], overrides: dict[str, object]
) -> None:
    private, jwks = keys
    with pytest.raises(SchedulerAuthError):
        verify_scheduler_token(_token(private, **overrides), AUD, INVOKER, jwks)  # type: ignore[arg-type]


def test_an_unconfigured_clock_refuses_everything(
    keys: tuple[rsa.RSAPrivateKey, _Jwks],
) -> None:
    private, jwks = keys
    with pytest.raises(ClockNotConfigured):
        verify_scheduler_token(_token(private), "", INVOKER, jwks)  # type: ignore[arg-type]


# --- route ----------------------------------------------------------------------


def test_the_clock_route_needs_a_scheduler_token(
    service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[get_service] = lambda: service
    try:
        client = TestClient(app)
        assert client.post("/internal/clock").status_code == 401
        refused = client.post("/internal/clock", headers={"Authorization": "Bearer x"})
        assert refused.status_code == 503  # no audience/invoker configured

        monkeypatch.setattr(world_routes, "verify_scheduler_token", lambda *a: None)
        ok = client.post("/internal/clock", headers={"Authorization": "Bearer x"})
        assert ok.status_code == 200 and ok.json() == {"ticked": [], "skipped": []}
    finally:
        app.dependency_overrides.clear()


def test_orders_for_a_due_turn_are_refused(
    repo: FakeGameRepo, service: GameService, clock: Clock
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1, clock="twice_daily")
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None
    service.submit_orders(player, [], [])
    clock.now = datetime(2026, 11, 10, 18, 0, 1, tzinfo=UTC)  # due, not yet ticked
    with pytest.raises(TurnClosed):
        service.submit_orders(player, [], [])


def test_orders_that_lose_the_race_to_a_tick_are_refused(
    repo: FakeGameRepo, service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=1)
    service.join_world(game_id, "user-1")
    player = repo.get_player(game_id, "user-1")
    assert player is not None
    store = repo.replace_orders

    def store_then_tick(*args: object) -> None:
        store(*args)  # type: ignore[arg-type]
        service.tick_world(game_id)  # the tick commits right after the upsert

    monkeypatch.setattr(repo, "replace_orders", store_then_tick)
    with pytest.raises(TurnClosed):
        service.submit_orders(player, [], [])


def test_worlds_are_listed_and_dispatch_knows_a_world(
    repo: FakeGameRepo, service: GameService
) -> None:
    game_id = service.create_world(slots=4, seed=3, bots=2, clock="twice_daily")
    service.join_world(game_id, "user-1")
    [summary] = service.list_worlds()
    assert (summary.game_id, summary.slots, summary.players, summary.humans) == (
        game_id,
        4,
        3,
        1,
    )
    assert summary.clock == "twice_daily" and summary.next_tick_at is not None
    active = service.get_dispatch("user-1").active_game
    assert active is not None and active.kind == "world"
    match = service.create_game("twin_rivers", "user-2")
    dispatch = service.get_dispatch("user-2").active_game
    assert dispatch is not None and dispatch.game_id == match.game_id
    assert dispatch.kind == "match"
