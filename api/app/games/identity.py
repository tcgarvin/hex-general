"""Player identity for async two-player play (roadmap Phase 5).

v0.1 uses lightweight opaque bearer tokens rather than full accounts (deferred):
each `players` row carries a random ``token`` that a client presents in the
``X-Player-Token`` header. The token is the only thing that proves a caller owns
a given game slot, so it is generated with `secrets` (cryptographically random,
URL-safe) and must never be exposed to the opposing player's browser.
"""

from __future__ import annotations

import secrets

# Header a client sends to authenticate as a particular player (game slot).
PLAYER_TOKEN_HEADER = "X-Player-Token"

# Bytes of entropy behind each token; token_urlsafe yields ~1.3 chars per byte.
_TOKEN_BYTES = 32


def generate_player_token() -> str:
    """Return a fresh, cryptographically-random opaque player token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)
