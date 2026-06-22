"""Supabase JWT verification — the identity boundary (roadmap Phase 7).

Phase 7 replaces the Phase 5 opaque ``X-Player-Token`` with real accounts: the
frontend holds a Supabase Auth session and sends the Supabase **JWT** as
``Authorization: Bearer <jwt>``. This module is the **boundary** (per CLAUDE.md):
it validates that token's signature, audience, and expiry against the project's
public keys and resolves it to a ``user_id`` (the JWT ``sub``). Anything missing,
malformed, or expired raises `AuthError`, which the route layer maps to 401.

The deployed Supabase project signs tokens with an asymmetric key (ES256) and
publishes the public half at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``; we
verify with that JWKS, so the signing secret never leaves Supabase. The
``PyJWKClient`` fetches and caches the key set, keyed by the token's ``kid``.

A *dev role* rides alongside identity: `is_dev` consults the ``DEV_USER_IDS``
allowlist (config) to decide whether a caller may impersonate another user via the
dev profile switcher. The impersonation mechanics live in the route dependencies;
this module only answers "is this user a dev?".
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from pydantic import BaseModel, ConfigDict

from app.config import get_settings

# Supabase stamps user tokens with this audience; reject anything else.
_AUDIENCE = "authenticated"
_ALGORITHMS = ["ES256"]


class AuthError(Exception):
    """The bearer token was missing, malformed, expired, or wrongly signed."""


class AuthIdentity(BaseModel):
    """A verified caller identity resolved from a Supabase JWT."""

    model_config = ConfigDict(frozen=True)

    user_id: str


@lru_cache
def _jwks_client() -> PyJWKClient:
    """Return a process-wide JWKS client for the configured Supabase project.

    `PyJWKClient` caches fetched keys internally, so verification does not hit the
    JWKS endpoint on every request.
    """
    settings = get_settings()
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url)


def verify_jwt(token: str) -> AuthIdentity:
    """Verify a Supabase access token and return its caller identity.

    Raises `AuthError` (never returns a partial/anonymous identity) on any
    signature, audience, expiry, or claim problem.
    """
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=_AUDIENCE,
        )
    except (jwt.InvalidTokenError, PyJWKClientError) as exc:
        raise AuthError(f"invalid Supabase token: {exc}") from exc

    user_id = claims.get("sub")
    if not user_id:
        raise AuthError("token has no subject (sub) claim")
    return AuthIdentity(user_id=user_id)


def is_dev(user_id: str) -> bool:
    """Whether ``user_id`` is allowlisted as a dev (may impersonate others)."""
    return user_id in get_settings().dev_user_id_set
