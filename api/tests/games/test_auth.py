"""Unit tests for the JWT identity boundary (roadmap Phase 7).

Covers the `current_identity` route dependency directly (bearer parsing, the
401/403 mapping, and dev impersonation gating) with `verify_jwt`/`is_dev` faked,
plus the dev-allowlist parsing in config. End-to-end JWT signature verification is
exercised against a real Supabase session in manual/staging checks, not here.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.games import routes
from app.games.auth import AuthError, AuthIdentity


def _identity(user_id: str) -> AuthIdentity:
    return AuthIdentity(user_id=user_id)


def test_missing_authorization_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        routes.real_identity(authorization=None)
    assert exc.value.status_code == 401


def test_non_bearer_scheme_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        routes.real_identity(authorization="Basic abc")
    assert exc.value.status_code == 401


def test_valid_bearer_returns_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "verify_jwt", lambda token: _identity("user-1"))
    identity = routes.real_identity(authorization="Bearer good")
    assert identity.user_id == "user-1"


def test_invalid_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(token: str) -> AuthIdentity:
        raise AuthError("nope")

    monkeypatch.setattr(routes, "verify_jwt", _raise)
    with pytest.raises(HTTPException) as exc:
        routes.real_identity(authorization="Bearer bad")
    assert exc.value.status_code == 401


def test_no_impersonation_returns_real_identity() -> None:
    identity = routes.current_identity(identity=_identity("user-1"), x_dev_profile=None)
    assert identity.user_id == "user-1"


def test_dev_may_impersonate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "is_dev", lambda user_id: user_id == "dev-1")
    identity = routes.current_identity(
        identity=_identity("dev-1"), x_dev_profile="victim"
    )
    assert identity.user_id == "victim"  # effective identity is the impersonated user


def test_non_dev_impersonation_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "is_dev", lambda user_id: False)
    with pytest.raises(HTTPException) as exc:
        routes.current_identity(identity=_identity("rando"), x_dev_profile="victim")
    assert exc.value.status_code == 403


def test_dev_user_id_set_parses_csv() -> None:
    settings = Settings(
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="k",
        dev_user_ids=" a , b ,, c ",
    )
    assert settings.dev_user_id_set == frozenset({"a", "b", "c"})


def test_dev_user_id_set_empty_by_default() -> None:
    settings = Settings(
        supabase_url="https://x.supabase.co", supabase_service_role_key="k"
    )
    assert settings.dev_user_id_set == frozenset()
