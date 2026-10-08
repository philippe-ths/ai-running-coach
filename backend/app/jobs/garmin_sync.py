"""Daily Garmin recovery sync for the deployment owner (#555).

A self-scheduling chain: each run does its work, then (in `finally`, so a failed
run still keeps the chain alive) schedules the next one at the next
`GARMIN_SYNC_HOUR_UTC` through `queue.enqueue_in`, drained by the worker's
embedded scheduler like every other deferred job (ADR 0006). Nothing is
scheduled and nothing calls Garmin unless `GARMIN_SYNC_ENABLED` is on.

`start_chain_if_needed` is called once at worker boot. A Redis `SET NX` marker
that outlives the scheduled gap stops a restart (every deploy) from starting a
second chain; if the chain is ever lost the marker expires and the next boot
restarts it. The first run for an owner with no stored Garmin nights backfills
`GARMIN_BACKFILL_DAYS`; later runs re-fetch only the last few nights.

NOTE: RQ serialises a deferred job as its `module.function` string, so
`garmin_sync_job` must keep this module path (tests/test_cadence_seam.py contract).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.recovery import store
from app.services.recovery.garmin_adapter import SOURCE, GarminRecoverySource
from app.services.recovery.sync import DAILY_LOOKBACK_DAYS, sync_recent

logger = logging.getLogger(__name__)

_CHAIN_KEY = "garmin:sync:chain"
# The marker outlives the gap to the next run by this margin, so a slightly late
# scheduler never lets it lapse and start a duplicate chain.
_CHAIN_MARGIN_SECONDS = 3600


def seconds_until_next_run(now: datetime, hour_utc: int) -> int:
    """Seconds from ``now`` to the next ``hour_utc``:00 UTC strictly in the future."""
    now = now.astimezone(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def _schedule_next(now: Optional[datetime] = None) -> None:
    from app.core.queue import queue, redis_conn

    delay = seconds_until_next_run(
        now or datetime.now(timezone.utc), settings.GARMIN_SYNC_HOUR_UTC
    )
    redis_conn.set(_CHAIN_KEY, "1", ex=delay + _CHAIN_MARGIN_SECONDS)
    queue.enqueue_in(
        timedelta(seconds=delay),
        garmin_sync_job,
        job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
    )


def start_chain_if_needed() -> bool:
    """Worker-boot hook: begin the chain unless one is already alive. True if started."""
    if not settings.GARMIN_SYNC_ENABLED:
        return False
    from app.core.queue import queue, redis_conn

    try:
        if not redis_conn.set(_CHAIN_KEY, "1", nx=True, ex=_CHAIN_MARGIN_SECONDS):
            return False
        # The run reschedules its own successor, replacing the short marker above.
        queue.enqueue(garmin_sync_job, job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001 - never stop the worker from booting
        logger.warning("garmin chain start failed: %s", type(exc).__name__)
        return False


def run_garmin_sync(db, *, source=None, today=None):
    """One sync pass for the owner. Returns the SyncResult, or None when skipped."""
    from app.services.notifications import resolve_owner_user

    owner = resolve_owner_user(db)
    if owner is None:
        logger.warning("garmin sync skipped: deployment owner not identifiable")
        return None
    if source is None:
        from app.core.queue import redis_conn

        source = GarminRecoverySource(
            settings.GARMIN_TOKENS.get_secret_value(), redis_conn
        )
    days = (
        DAILY_LOOKBACK_DAYS
        if store.has_any(db, owner.id, SOURCE)
        else settings.GARMIN_BACKFILL_DAYS
    )
    today = today or datetime.now(timezone.utc).date()
    result = sync_recent(db, owner.id, source, today=today, days=days)
    logger.info(
        "garmin sync: days=%d written=%d empty=%d failed=%d auth_failed=%s",
        days, result.written, result.skipped_empty, result.failed, result.auth_failed,
    )
    return result


def garmin_sync_job() -> None:
    """RQ entrypoint. Disabled means a clean no-op that does not reschedule."""
    if not settings.GARMIN_SYNC_ENABLED:
        return
    db = SessionLocal()
    try:
        run_garmin_sync(db)
    except Exception as exc:  # noqa: BLE001 - the chain must survive any failure
        db.rollback()
        logger.error("garmin sync run failed: %s", type(exc).__name__)
    finally:
        db.close()
        try:
            _schedule_next()
        except Exception as exc:  # noqa: BLE001
            logger.error("garmin sync could not schedule its next run: %s", type(exc).__name__)
