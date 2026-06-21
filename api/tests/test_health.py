from fastapi.testclient import TestClient

from app import __version__
from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_app_version() -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": __version__}
