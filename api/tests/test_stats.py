from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from supabase import Client

from app.db import get_supabase_client
from app.main import app
from app.stats import count_active_users


class _FakeQuery:
    """Stand-in for a postgrest query builder that records the configured count."""

    def __init__(self, count: int | None) -> None:
        self._count = count

    def select(self, *args: object, **kwargs: object) -> "_FakeQuery":
        return self

    def gte(self, *args: object, **kwargs: object) -> "_FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(count=self._count, data=[])


class _FakeClient:
    """Minimal Supabase client returning a fixed active-user count."""

    def __init__(self, count: int | None) -> None:
        self._count = count

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._count)


def _fake_client(count: int | None) -> Client:
    return cast(Client, _FakeClient(count))


def test_count_active_users_returns_count() -> None:
    assert count_active_users(_fake_client(3)) == 3


def test_count_active_users_zero_when_empty() -> None:
    assert count_active_users(_fake_client(0)) == 0


def test_count_active_users_raises_without_count() -> None:
    with pytest.raises(RuntimeError):
        count_active_users(_fake_client(None))


def test_stats_endpoint_reports_active_users() -> None:
    app.dependency_overrides[get_supabase_client] = lambda: _fake_client(0)
    try:
        client = TestClient(app)
        response = client.get("/stats")
        assert response.status_code == 200
        assert response.json() == {"active_users": 0}
    finally:
        app.dependency_overrides.clear()
