"""Unit tests for the local dev-account seed (`app.dev_seed`).

Exercise the pure roster/idempotency logic directly, and drive `seed_dev_users`
against a fake Supabase admin client so the create-vs-reuse decision and
pagination are covered without real I/O (CLAUDE.md: mock external services).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dev_seed import (
    DEV_PASSWORD,
    DEV_USER_EMAILS,
    DevUserSpec,
    SeededUser,
    dev_user_specs,
    seed_dev_users,
    users_to_create,
)


@dataclass(frozen=True)
class _FakeUser:
    id: str
    email: str | None


@dataclass(frozen=True)
class _FakeUserResponse:
    user: _FakeUser | None


class _FakeAdmin:
    """Paginates like the real admin API and records created accounts."""

    def __init__(self, users: list[_FakeUser]) -> None:
        self._users = list(users)
        self.created: list[dict[str, object]] = []
        self._next_id = 0

    def list_users(self, page: int, per_page: int) -> list[_FakeUser]:
        start = (page - 1) * per_page
        return self._users[start : start + per_page]

    def create_user(self, attributes: dict[str, object]) -> _FakeUserResponse:
        self.created.append(attributes)
        self._next_id += 1
        user = _FakeUser(id=f"new-{self._next_id}", email=str(attributes["email"]))
        self._users.append(user)
        return _FakeUserResponse(user=user)


class _FakeAuth:
    def __init__(self, admin: _FakeAdmin) -> None:
        self.admin = admin


class _FakeClient:
    def __init__(self, users: list[_FakeUser]) -> None:
        self.auth = _FakeAuth(_FakeAdmin(users))


def test_roster_is_two_hex_local_accounts_sharing_the_password() -> None:
    specs = dev_user_specs()
    assert [s.email for s in specs] == list(DEV_USER_EMAILS)
    assert all(s.email.endswith("@hex.local") for s in specs)
    assert all(s.password == DEV_PASSWORD for s in specs)


def test_users_to_create_filters_out_existing_emails() -> None:
    specs = [DevUserSpec("a@hex.local", "p"), DevUserSpec("b@hex.local", "p")]
    assert users_to_create(specs, frozenset({"a@hex.local"})) == [specs[1]]
    assert users_to_create(specs, frozenset()) == specs
    assert users_to_create(specs, frozenset({"a@hex.local", "b@hex.local"})) == []


def test_seeds_missing_accounts_on_an_empty_stack() -> None:
    client = _FakeClient([])

    results = seed_dev_users(client)  # type: ignore[arg-type]

    assert all(r.created for r in results)
    assert [r.email for r in results] == list(DEV_USER_EMAILS)
    # Every account was created exactly once, pre-confirmed.
    assert len(client.auth.admin.created) == len(DEV_USER_EMAILS)
    assert all(a["email_confirm"] is True for a in client.auth.admin.created)


def test_is_idempotent_when_accounts_already_exist() -> None:
    existing = [
        _FakeUser("u-dev1", DEV_USER_EMAILS[0]),
        _FakeUser("u-dev2", DEV_USER_EMAILS[1]),
    ]
    client = _FakeClient(existing)

    results = seed_dev_users(client)  # type: ignore[arg-type]

    assert results == [
        SeededUser(email=DEV_USER_EMAILS[0], user_id="u-dev1", created=False),
        SeededUser(email=DEV_USER_EMAILS[1], user_id="u-dev2", created=False),
    ]
    # Nothing was created the second time around.
    assert client.auth.admin.created == []


def test_creates_only_the_missing_account() -> None:
    client = _FakeClient([_FakeUser("u-dev1", DEV_USER_EMAILS[0])])

    results = seed_dev_users(client)  # type: ignore[arg-type]

    assert results[0] == SeededUser(DEV_USER_EMAILS[0], "u-dev1", created=False)
    assert results[1].email == DEV_USER_EMAILS[1] and results[1].created is True
    assert [a["email"] for a in client.auth.admin.created] == [DEV_USER_EMAILS[1]]
