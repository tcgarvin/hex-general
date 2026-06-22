"""Seed local Supabase Auth with dev playtest accounts (roadmap Phase 7, Slice 3).

Run against a **local** `supabase start` stack so a dev can play a full two-sided
match on a laptop without Google OAuth: this creates the ``@hex.local`` accounts
that the dev-profile switcher impersonates. Like `app.accounts`, it is an I/O
boundary that uses the service-role admin API — server-side only, never the
browser.

Invoked by ``scripts/dev-up.sh`` (or directly: ``uv run python -m app.dev_seed``).
It is **idempotent**: accounts that already exist are reused, not recreated. On
success it prints a ready-to-paste ``DEV_USER_IDS=<id1>,<id2>`` line, which the
API needs to treat these accounts as devs (the allowlist can only be filled once
the users exist — `app.config.Settings.dev_user_id_set`).

This module never points at the deployed project on its own: it builds its client
from ``SUPABASE_URL`` / ``SUPABASE_SERVICE_ROLE_KEY`` (see `app.db`), which the
local loop sets to ``http://127.0.0.1:54321`` and the local service-role key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from supabase import Client
from supabase_auth.types import AdminUserAttributes

from app.accounts import DEV_EMAIL_DOMAIN
from app.db import get_supabase_client

# Shared password for every seeded dev account. LOCAL-ONLY: these accounts exist
# solely on a laptop's `supabase start` stack and never on the deployed project,
# so a fixed, well-known password is intentional (it is how you log in to play).
DEV_PASSWORD = "devpass123"

# The roster of playtest identities. Two is enough to drive both sides of a match
# via the dev switcher; emails share the @hex.local domain so `app.accounts`
# already surfaces them in the switcher.
DEV_USER_EMAILS: tuple[str, ...] = (
    f"dev1{DEV_EMAIL_DOMAIN}",
    f"dev2{DEV_EMAIL_DOMAIN}",
)

# list_users paginates; mirror app.accounts' generous page size.
_PAGE_SIZE = 200


@dataclass(frozen=True)
class DevUserSpec:
    """A dev account to ensure exists in local Auth."""

    email: str
    password: str


@dataclass(frozen=True)
class SeededUser:
    """The outcome for one roster entry after seeding."""

    email: str
    user_id: str
    created: bool


def dev_user_specs() -> list[DevUserSpec]:
    """The full dev roster (pure — no I/O), one spec per playtest identity."""
    return [
        DevUserSpec(email=email, password=DEV_PASSWORD) for email in DEV_USER_EMAILS
    ]


def users_to_create(
    specs: list[DevUserSpec], existing_emails: frozenset[str]
) -> list[DevUserSpec]:
    """The subset of ``specs`` not already present (pure — drives idempotency)."""
    return [spec for spec in specs if spec.email not in existing_emails]


def _existing_users_by_email(client: Client) -> dict[str, str]:
    """Map email -> user_id for every existing Auth account (paginated read)."""
    by_email: dict[str, str] = {}
    page = 1
    while True:
        users = client.auth.admin.list_users(page=page, per_page=_PAGE_SIZE)
        for user in users:
            if user.email:
                by_email[user.email] = user.id
        if len(users) < _PAGE_SIZE:
            break
        page += 1
    return by_email


def _create_dev_user(client: Client, spec: DevUserSpec) -> str:
    """Create one pre-confirmed dev account and return its user id.

    ``email_confirm=True`` marks the address confirmed so the account can sign in
    immediately (local Auth has no SMTP). Raises if Auth returns no user — a
    boundary failure we must not paper over.
    """
    attributes = cast(
        AdminUserAttributes,
        {"email": spec.email, "password": spec.password, "email_confirm": True},
    )
    response = client.auth.admin.create_user(attributes)
    if response.user is None:
        raise RuntimeError(f"Auth admin API returned no user for {spec.email!r}")
    return response.user.id


def seed_dev_users(client: Client) -> list[SeededUser]:
    """Ensure every roster account exists; return the outcome for each.

    Idempotent: existing accounts (matched by email) are reused; only the missing
    ones are created.
    """
    specs = dev_user_specs()
    existing = _existing_users_by_email(client)

    results: list[SeededUser] = []
    for spec in specs:
        existing_id = existing.get(spec.email)
        if existing_id is not None:
            results.append(
                SeededUser(email=spec.email, user_id=existing_id, created=False)
            )
        else:
            user_id = _create_dev_user(client, spec)
            results.append(SeededUser(email=spec.email, user_id=user_id, created=True))
    return results


def main() -> None:
    """CLI entrypoint: seed the local stack and print ids + the DEV_USER_IDS line."""
    client = get_supabase_client()
    seeded = seed_dev_users(client)

    print(f"Seeded dev accounts (password for all: {DEV_PASSWORD}):")
    for user in seeded:
        status = "created" if user.created else "exists "
        print(f"  [{status}] {user.email}  ({user.user_id})")

    ids = ",".join(user.user_id for user in seeded)
    print()
    print("Set this in api/.env so the API treats them as devs:")
    print(f"DEV_USER_IDS={ids}")


if __name__ == "__main__":
    main()
