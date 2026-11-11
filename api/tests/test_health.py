import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_app_version_and_build_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": __version__, "commit": "abc123"}


def test_version_outside_a_ci_image_reports_a_local_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert client.get("/version").json()["commit"] == "local"
