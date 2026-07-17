"""Strava access-token lifecycle: freshness, refresh, and per-account
refresh serialization.

Split out of the batch orchestrator (#702) so token handling is testable in
isolation from ingestion. A Strava refresh ROTATES the refresh token, so
concurrent refreshes for one account race; the Redis lock here serializes them
(#597).
"""

import logging
import time

from sqlalchemy.orm import Session

from app.core.queue import redis_conn
from app.models import StravaAccount
from app.services.strava_ingestion.port import StravaPort, Tokens

logger = logging.getLogger(__name__)

# Buffer in seconds before token expiry we'll trigger a refresh.
_TOKEN_REFRESH_BUFFER_S = 60

# Per-account refresh serialization (#597). A Strava token refresh ROTATES
# (invalidates) the refresh token, so two concurrent refreshes for one account
# race: the second runs with a now-stale refresh token and can permanently
# unlink the account. We serialize refresh per account with a short Redis lock.
#
# TIMEOUT auto-releases the lock if a holder dies mid-refresh (avoids a
# deadlock). It MUST exceed the worst-case hold — a refresh HTTP call can take up
# to the adapter's _HTTP_TIMEOUT_S (30s) plus the DB commit — or the lock could
# expire while the winner is still refreshing and a waiter would double-refresh,
# defeating the serialization. WAIT bounds how long a concurrent caller blocks
# for the winner (refreshes are typically ~1-2s); on timeout, or if Redis is
# unavailable, we degrade to an unlocked refresh rather than block ingestion —
# best-effort serialization, never a hard dependency.
_TOKEN_REFRESH_LOCK_TIMEOUT_S = 60
_TOKEN_REFRESH_LOCK_WAIT_S = 30


def _token_is_fresh(account: StravaAccount) -> bool:
    return account.expires_at > time.time() + _TOKEN_REFRESH_BUFFER_S


async def ensure_valid_access_token(
    db: Session, account: StravaAccount, port: StravaPort, *, force: bool = False
) -> str:
    """Return a valid access token, refreshing and persisting if needed.

    Pass force=True to unconditionally refresh (e.g. after a mid-flight 401).

    Refresh is serialized per account behind a Redis lock (#597): a Strava
    refresh rotates the refresh token, so concurrent refreshes for one account
    race and the loser can unlink it. The winner refreshes; a loser, on acquiring
    the lock, RE-READS the row and — if the winner already rotated the token —
    reuses it instead of refreshing again with a stale refresh token.
    """
    if not force and _token_is_fresh(account):
        return account.access_token

    original_access = account.access_token
    lock = _acquire_refresh_lock(account)
    if lock is None:
        # Redis unavailable / not acquired: degrade to an unlocked refresh so a
        # coordination outage never blocks ingestion.
        return await _do_refresh(db, account, port)
    try:
        # A concurrent caller may have refreshed while we waited. Re-read the row
        # (READ COMMITTED sees the winner's commit) and, if the access token has
        # changed, reuse it rather than burning the freshly-rotated refresh token.
        db.refresh(account)
        if account.access_token != original_access:
            return account.access_token
        if not force and _token_is_fresh(account):
            return account.access_token
        return await _do_refresh(db, account, port)
    finally:
        _release_refresh_lock(lock)


def _acquire_refresh_lock(account: StravaAccount):
    """Acquire the per-account refresh lock, or None if it can't be taken.

    Returns the held lock (release it via _release_refresh_lock) or None when
    Redis is unavailable or the wait times out, in which case the caller
    refreshes unlocked rather than failing."""
    try:
        lock = redis_conn.lock(
            f"strava_token_refresh:{account.id}",
            timeout=_TOKEN_REFRESH_LOCK_TIMEOUT_S,
            blocking_timeout=_TOKEN_REFRESH_LOCK_WAIT_S,
        )
        if lock.acquire():
            return lock
        logger.warning(
            "strava_token_refresh_lock_timeout account=%s; refreshing unlocked",
            account.id,
        )
        return None
    except Exception as exc:  # Redis down / misconfigured: degrade, do not block
        logger.warning(
            "strava_token_refresh_lock_unavailable account=%s: %s; refreshing unlocked",
            account.id,
            exc,
        )
        return None


def _release_refresh_lock(lock) -> None:
    try:
        lock.release()
    except Exception:  # lock already expired (TTL) or owned by another holder
        pass


async def _do_refresh(
    db: Session, account: StravaAccount, port: StravaPort
) -> str:
    tokens = await port.refresh_token(account.refresh_token)
    _apply_tokens(account, tokens)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account.access_token


def _apply_tokens(account: StravaAccount, tokens: Tokens) -> None:
    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.expires_at = tokens.expires_at
