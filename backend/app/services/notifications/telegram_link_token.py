"""One-time token for binding a runner's Telegram chat to their account (#477).

The runner taps a `https://t.me/<bot>?start=<token>` deep link; Telegram opens
the bot with the token as the `/start` argument. The inbound `/start` carries no
Clerk session, so the token is what tells the bot WHICH signed-in user is
binding. Telegram's `start` parameter is limited to <=64 chars of `[A-Za-z0-9_-]`,
which rules out the HMAC OAuth-state encoding (too long, contains `.`).

We use a genuinely single-use Redis nonce instead: `mint` stores a random token
keyed to the user id with a short TTL, and `consume` atomically reads-and-deletes
it (`GETDEL`), so a replayed or expired link no-ops. Redis is already wired for
the job queue, so this adds no new dependency.

`consume` never raises: a missing, expired, already-used, or malformed token
resolves to `None` (the caller then ignores the `/start`), mirroring the
never-raise contract of `callback_token.decode`.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Optional

from app.core.queue import redis_conn

logger = logging.getLogger(__name__)

_PREFIX = "tglink:"
_TTL_SECONDS = 600  # 10 minutes; the runner taps the link almost immediately


def mint(user_id: uuid.UUID | str, *, ttl_seconds: int = _TTL_SECONDS) -> str:
    """Create a single-use link token bound to ``user_id`` and return it.

    The token is URL-safe and well under Telegram's 64-char `start` limit.
    """
    nonce = secrets.token_urlsafe(16)  # ~22 chars, [A-Za-z0-9_-]
    redis_conn.set(f"{_PREFIX}{nonce}", str(user_id), ex=ttl_seconds)
    return nonce


def consume(token: str) -> Optional[uuid.UUID]:
    """Atomically redeem a link token, returning its user_id or ``None``.

    Single-use: the token is deleted as it is read, so a replay finds nothing.
    Never raises -- a missing/expired/used/malformed token (or a Redis error)
    yields ``None``.
    """
    if not token:
        return None
    try:
        raw = redis_conn.getdel(f"{_PREFIX}{token}")
    except Exception:  # noqa: BLE001 -- a Redis hiccup must not 500 the webhook
        logger.exception("telegram link-token consume failed")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None
