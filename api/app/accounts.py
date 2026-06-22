"""Account lookup boundary for the dev profile switcher (roadmap Phase 7).

An **I/O boundary**: it reads Supabase Auth accounts via the service-role admin
API so a dev can pick who to impersonate from a menu (their own account plus the
``@hex.local`` test accounts) instead of pasting a raw user id. Server-side only —
the admin API is gated by the service-role key and must never reach the browser.
"""

from __future__ import annotations

from supabase import Client

from app.games.schemas import DevProfile

# Test accounts created for solo playtesting all share this email domain.
DEV_EMAIL_DOMAIN = "@hex.local"

# Admin list_users paginates; pull a generous page so a single request covers the
# handful of accounts a pre-launch project has, while still paging if it grows.
_PAGE_SIZE = 200


def list_dev_profiles(client: Client, self_user_id: str) -> list[DevProfile]:
    """Return the accounts selectable in the dev switcher.

    The calling dev's own account is always included (so they can switch back to
    themselves), alongside every ``@hex.local`` test account. The self account is
    sorted first; the rest follow by email.
    """
    profiles: dict[str, DevProfile] = {}
    page = 1
    while True:
        users = client.auth.admin.list_users(page=page, per_page=_PAGE_SIZE)
        for user in users:
            email = user.email or ""
            is_self = user.id == self_user_id
            if is_self or email.endswith(DEV_EMAIL_DOMAIN):
                profiles[user.id] = DevProfile(
                    user_id=user.id, email=email, is_self=is_self
                )
        if len(users) < _PAGE_SIZE:
            break
        page += 1

    return sorted(profiles.values(), key=lambda p: (not p.is_self, p.email))
