"""Server-verifiable OAuth `state` token for the Strava connect flow (#469).

The Strava OAuth callback is a direct browser redirect from Strava and carries
no Clerk session, so it cannot tell which signed-in user is connecting. To link
a new Strava account to the right user under multi-user, `/auth/strava/login`
mints a short-lived HMAC-signed `state` carrying the authenticated `user_id`;
the callback verifies it and attaches the StravaAccount to that user.

The token is HMAC-SHA256 over ``"{user_id}.{exp}"`` (a Unix expiry), encoded
base64url so it survives the un-encoded query builder in the Strava adapter. An
attacker cannot forge a state for a victim's user_id without the signing secret,
and the short TTL bounds replay. ``decode_state`` never raises: a malformed,
tampered, or expired token resolves to ``None``; in production the callback then
rejects the new-athlete link and prompts a reconnect (#599), while non-production
falls back to the single-owner path. Mirrors ``callback_token.decode``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
import uuid
from typing import Optional

from app.core.config import settings

# 30 minutes. A runner can linger on Strava's consent screen for several minutes
# before granting; a too-short TTL expires the state and, in production, forces a
# reconnect (#599). Long enough to cover a slow consent, short enough to bound replay.
_DEFAULT_TTL_SECONDS = 1800


def _signing_secret() -> bytes:
    """The HMAC key, secure-by-default in prod without a dedicated env var.

    Prefers the dedicated ``STRAVA_OAUTH_STATE_SECRET`` (rotate the OAuth state
    alone), else reuses an existing server-side secret that is set in prod
    (``BASIC_AUTH_PASSWORD`` is required for the web process to boot, #480; then
    ``CLERK_SECRET_KEY``). With no server secret at all (pure local dev) the key
    is empty, which is acceptable: that path is single-owner with no adversary
    and the callback falls back to the single existing user anyway.
    """
    secret = (
        settings.STRAVA_OAUTH_STATE_SECRET
        or settings.BASIC_AUTH_PASSWORD
        or settings.CLERK_SECRET_KEY
        or ""
    )
    return secret.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)


def encode_state(user_id: uuid.UUID | str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    """Mint a signed, URL-safe state token binding ``user_id`` with a short TTL."""
    exp = int(time.time()) + ttl_seconds
    payload = f"{user_id}.{exp}".encode("utf-8")
    sig = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def decode_state(token: str) -> Optional[uuid.UUID]:
    """Verify a state token and return its user_id, or ``None`` if unusable.

    Returns ``None`` for any malformed, tampered, expired, or non-UUID token;
    never raises.
    """
    if not token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None

    expected = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        user_id_str, exp_str = payload.decode("utf-8").rsplit(".", 1)
        exp = int(exp_str)
    except (ValueError, UnicodeDecodeError):
        return None

    if exp < int(time.time()):
        return None

    try:
        return uuid.UUID(user_id_str)
    except ValueError:
        return None
