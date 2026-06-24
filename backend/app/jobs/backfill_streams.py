"""Backfill stream-derived analysis for historical summary-only activities.

A manually-triggered, self-pacing job. Each run processes a bounded batch of
activities that still lack stream-derived analysis (imported summary-only by a
historical `POST /api/sync` backfill), fetching streams and re-running analysis
per activity. It never notifies.

State lives entirely in the DB: an activity is eligible when it has no
`ActivityStream` rows and its `streams_backfilled_at` is unset. Each attempt sets
that marker (even when Strava has no streams for the activity), so an
interruption resumes without re-fetching completed work and every eligible
activity is attempted exactly once, guaranteeing convergence.

Pacing: when work remains after a batch, the job schedules its own successor via
rq-scheduler `enqueue_in` after `BACKFILL_BATCH_PAUSE_SECONDS`, keeping the
worker free between batches and the combined Strava call rate under the
100-requests/15-min ceiling alongside polling. See #110.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import Activity, ActivityStream
from app.services.analysis import analyze_with_streams

logger = logging.getLogger(__name__)

_BACKFILL_JOB_ID = "backfill_streams"


def _eligible_stmt():
    """Activities lacking stream-derived analysis and not yet attempted.

    Excludes activities that already have streams (the normal new-activity
    pipeline fetched them) and activities already attempted by an earlier
    backfill run, so genuinely streamless activities are attempted once and the
    job converges. Newest first, since recent runs are the most likely to be
    opened.
    """
    has_streams = (
        select(ActivityStream.id)
        .where(ActivityStream.activity_id == Activity.id)
        .exists()
    )
    return (
        select(Activity)
        .where(
            Activity.is_deleted.is_(False),
            Activity.streams_backfilled_at.is_(None),
            ~has_streams,
        )
        .order_by(Activity.start_date.desc())
    )


def count_eligible(db: Session) -> int:
    """How many activities still need stream-derived analysis."""
    return len(db.execute(_eligible_stmt()).scalars().all())


@dataclass
class BackfillBatchResult:
    processed: list[int] = field(default_factory=list)  # strava_activity_ids
    remaining: int = 0


async def backfill_streams_batch(db: Session, *, limit: int) -> BackfillBatchResult:
    """Process up to `limit` eligible activities: fetch streams + re-analyze.

    Marks each attempted activity via `streams_backfilled_at` in a `finally`, so
    even a hard failure counts as attempted and the job converges (transient
    Strava errors are already retried inside the HTTP adapter). Commits per
    activity so progress survives an interruption. Never notifies.
    """
    batch = db.execute(_eligible_stmt().limit(limit)).scalars().all()
    result = BackfillBatchResult()

    for activity in batch:
        strava_id = activity.strava_activity_id
        try:
            await analyze_with_streams(db, str(activity.id))
        except Exception as exc:  # noqa: BLE001 - convergence over completeness
            logger.error(
                "Backfill failed for activity %s (strava id %s): %s",
                activity.id,
                strava_id,
                exc,
            )
        finally:
            activity.streams_backfilled_at = datetime.now(timezone.utc)
            db.add(activity)
            db.commit()
            result.processed.append(strava_id)

    result.remaining = count_eligible(db)
    logger.info(
        "Backfill batch processed %d activities, %d remaining",
        len(result.processed),
        result.remaining,
    )
    return result


def _schedule_next_batch() -> None:
    """Schedule the next batch after the configured pause, so the single worker
    stays free to process webhooks between batches.

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006)."""
    from app.core.queue import queue

    queue.enqueue_in(
        timedelta(seconds=settings.BACKFILL_BATCH_PAUSE_SECONDS),
        backfill_streams_job,
    )


def backfill_streams_job() -> None:
    """RQ entrypoint. Runs one batch; if work remains, schedules the next."""
    db = SessionLocal()
    try:
        result = asyncio.run(
            backfill_streams_batch(db, limit=settings.BACKFILL_BATCH_SIZE)
        )
    finally:
        db.close()

    if result.remaining > 0:
        _schedule_next_batch()
        logger.info(
            "Backfill scheduled next batch in %ds (%d remaining)",
            settings.BACKFILL_BATCH_PAUSE_SECONDS,
            result.remaining,
        )
    else:
        logger.info("Backfill complete: no eligible activities remain")


def enqueue_backfill(db: Session) -> int:
    """Count eligible activities and enqueue the first backfill batch.

    Returns the eligible count. A fixed job id keeps a re-trigger from starting a
    second chain while one is in flight; the `streams_backfilled_at` marker makes
    any overlap idempotent regardless.
    """
    eligible = count_eligible(db)
    if eligible:
        queue.enqueue(
            backfill_streams_job,
            job_id=_BACKFILL_JOB_ID,
            result_ttl=3600,
        )
    return eligible
