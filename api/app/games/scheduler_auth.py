"""Authenticating the turn clock's caller: Cloud Scheduler, by Google OIDC token.

The API is public (the browser calls it), so Cloud Run's own IAM check can't guard
the clock endpoint. Instead Cloud Scheduler sends a Google-signed ID token for a
dedicated service account, and this module verifies it: Google's signature (keys
from Google's JWKS, cached by `PyJWKClient`), a Google issuer, the configured
audience, and the configured service-account email, verified.

Both settings (`Settings.clock_audience`, `Settings.clock_invoker_email`) must be
set: with either empty the endpoint refuses every call rather than running open.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})


class SchedulerAuthError(Exception):
    """The token is missing, invalid, or not from the configured scheduler."""


class ClockNotConfigured(Exception):
    """The clock endpoint has no audience or invoker configured."""


@lru_cache
def google_jwks() -> PyJWKClient:
    """Google's OIDC signing keys (fetched lazily, then cached)."""
    return PyJWKClient(GOOGLE_JWKS_URL)


def verify_scheduler_token(
    token: str,
    audience: str,
    invoker_email: str,
    jwks: PyJWKClient | None = None,
) -> None:
    """Raise unless ``token`` is a valid Google ID token for ``invoker_email``.

    Raises `ClockNotConfigured` if ``audience`` or ``invoker_email`` is empty and
    `SchedulerAuthError` for any token problem.
    """
    if not audience or not invoker_email:
        raise ClockNotConfigured("CLOCK_AUDIENCE and CLOCK_INVOKER_EMAIL must be set")
    try:
        key = (jwks or google_jwks()).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=audience
        )
    except (jwt.InvalidTokenError, PyJWKClientError) as exc:
        raise SchedulerAuthError(f"invalid scheduler token: {exc}") from exc
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise SchedulerAuthError(f"token issuer {claims.get('iss')!r} is not Google")
    if claims.get("email") != invoker_email or claims.get("email_verified") is not True:
        raise SchedulerAuthError(
            f"token is for {claims.get('email')!r}, not the clock's invoker"
        )
