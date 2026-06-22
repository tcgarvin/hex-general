"""Unit tests for the dev-profile account lookup (`app.accounts`).

Drive `list_dev_profiles` against a fake Supabase admin client so the filtering
(self + ``@hex.local``) and pagination are exercised without real I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.accounts import list_dev_profiles


@dataclass(frozen=True)
class _FakeUser:
    id: str
    email: str | None


class _FakeAdmin:
    """Paginates like the real admin API: each call returns a ``per_page`` slice."""

    def __init__(self, users: list[_FakeUser]) -> None:
        self._users = users

    def list_users(self, page: int, per_page: int) -> list[_FakeUser]:
        start = (page - 1) * per_page
        return self._users[start : start + per_page]


class _FakeAuth:
    def __init__(self, admin: _FakeAdmin) -> None:
        self.admin = admin


class _FakeClient:
    def __init__(self, users: list[_FakeUser]) -> None:
        self.auth = _FakeAuth(_FakeAdmin(users))


def test_lists_self_and_hex_local_accounts_excluding_other_real_users() -> None:
    client = _FakeClient(
        [
            _FakeUser("dev-1", "dev@gmail.com"),
            _FakeUser("test-blue", "blue@hex.local"),
            _FakeUser("test-red", "red@hex.local"),
            _FakeUser("stranger", "someone@gmail.com"),
        ]
    )

    profiles = list_dev_profiles(client, self_user_id="dev-1")  # type: ignore[arg-type]

    assert [(p.user_id, p.email, p.is_self) for p in profiles] == [
        ("dev-1", "dev@gmail.com", True),  # self sorts first
        ("test-blue", "blue@hex.local", False),
        ("test-red", "red@hex.local", False),
    ]


def test_self_appears_once_even_with_a_hex_local_email() -> None:
    client = _FakeClient([_FakeUser("dev-1", "dev@hex.local")])

    profiles = list_dev_profiles(client, self_user_id="dev-1")  # type: ignore[arg-type]

    assert len(profiles) == 1
    assert profiles[0].is_self is True


def test_pages_through_all_accounts() -> None:
    # More than one page (the loop requests 200 at a time) to exercise paging.
    users = [_FakeUser(f"u{i:03d}", f"u{i:03d}@hex.local") for i in range(250)]
    client = _FakeClient(users)

    profiles = list_dev_profiles(client, self_user_id="absent")  # type: ignore[arg-type]

    assert {p.user_id for p in profiles} == {u.id for u in users}
