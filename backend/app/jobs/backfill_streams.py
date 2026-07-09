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
100-requests/15-min ceiling. See #110.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import Activity, ActivityStream
from app.services.analysis import analyze_with_streams
from app.services.strava_ingestion import strava_budget
from app.services.strava_ingestion.port import StravaRateLimited

logger = logging.getLogger(__name__)

_BACKFILL_JOB_ID = "backfill_streams"

# Best-effort window collapsing rapid re-triggers of a user's backfill into one
# chain (#596/#601). Short: it only guards against a burst of syncs/taps, not the
# whole (minutes-to-hours) chain lifetime; the streams_backfilled_at marker keeps
# any later overlap idempotent.
_BACKFILL_ENQUEUE_COOLDOWN_S = 120


def _eligible_stmt(*, user_id: Optional[str] = None):
    """Activities lacking stream-derived analysis and not yet attempted.

    Excludes activities that already have streams (the normal new-activity
    pipeline fetched them) and activities already attempted by an earlier
    backfill run, so genuinely streamless activities are attempted once and the
    job converges. Newest first, since recent runs are the most likely to be
    opened. When `user_id` is given the set is scoped to that runner (#470), so a
    user's trigger only backfills their own history; omit it for a global pass.
    """
    has_streams = (
        select(ActivityStream.id)
        .where(ActivityStream.activity_id == Activity.id)
        .exists()
    )
    stmt = select(Activity).where(
        Activity.is_deleted.is_(False),
        Activity.streams_backfilled_at.is_(None),
        ~has_streams,
    )
    if user_id is not None:
        # user_id rides through RQ as a string; coerce so the Uuid column
        # comparison works on both Postgres and the SQLite test backend.
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)
        stmt = stmt.where(Activity.user_id == user_id)
    return stmt.order_by(Activity.start_date.desc())


def count_eligible(db: Session, *, user_id: Optional[str] = None) -> int:
    """How many activities still need stream-derived analysis (optionally scoped)."""
    return len(db.execute(_eligible_stmt(user_id=user_id)).scalars().all())


@dataclass
class BackfillBatchResult:
    processed: list[int] = field(default_factory=list)  # strava_activity_ids
    remaining: int = 0
    # Set when the batch stopped early because the shared Strava budget was
    # exhausted (or Strava rate-limited us) mid-batch. The job then reschedules
    # after the budget backoff instead of the normal batch pause (#601).
    budget_exhausted: bool = False


async def backfill_streams_batch(
    db: Session, *, limit: int, user_id: Optional[str] = None
) -> BackfillBatchResult:
    """Process up to `limit` eligible activities: fetch streams + re-analyze.

    Re-checks the shared Strava budget before EACH activity, not once per batch
    (#601): a batch of up to BACKFILL_BATCH_SIZE would otherwise fire that many
    uninterrupted streams calls after a single gate pass, so N concurrent per-user
    chains could overshoot Strava's 100/15min ceiling. On an exhausted budget — or
    a StravaRateLimited raised mid-batch (#602) — the loop stops and leaves the
    remaining activities eligible so they are retried once capacity frees.

    A genuine per-activity failure still marks `streams_backfilled_at` so the job
    converges (transient Strava errors are retried inside the HTTP adapter, and an
    activity Strava has no streams for must not be retried forever). Only the
    budget/rate-limit yield leaves an activity unmarked. Commits per activity so
    progress survives an interruption. Scoped to `user_id` when given (#470). Never
    notifies.
    """
    batch = db.execute(_eligible_stmt(user_id=user_id).limit(limit)).scalars().all()
    result = BackfillBatchResult()

    for activity in batch:
        if strava_budget.over_budget():
            result.budget_exhausted = True
            logger.info(
                "Backfill batch yielding mid-batch: Strava budget exhausted "
                "(%d processed, remaining left eligible)",
                len(result.processed),
            )
            break
        strava_id = activity.strava_activity_id
        try:
            await analyze_with_streams(db, str(activity.id))
        except StravaRateLimited as exc:
            # Shared window hit mid-batch: leave this activity eligible (do NOT
            # mark attempted) and stop so the job reschedules after the backoff.
            result.budget_exhausted = True
            logger.info(
                "Backfill batch yielding: Strava rate limited (retry_after=%s)",
                exc.retry_after,
            )
            break
        except Exception as exc:  # noqa: BLE001 - convergence over completeness
            logger.error(
                "Backfill failed for activity %s (strava id %s): %s",
                activity.id,
                strava_id,
                exc,
            )
        # Reached only for a genuine attempt (success or terminal-for-this-activity
        # failure), never the budget/rate-limit break above.
        activity.streams_backfilled_at = datetime.now(timezone.utc)
        db.add(activity)
        db.commit()
        result.processed.append(strava_id)

    result.remaining = count_eligible(db, user_id=user_id)
    logger.info(
        "Backfill batch processed %d activities, %d remaining",
        len(result.processed),
        result.remaining,
    )
    return result


def _schedule_next_batch(user_id: Optional[str] = None) -> None:
    """Schedule the next batch after the configured pause, so the single worker
    stays free to process webhooks between batches. Carries `user_id` so the
    self-paced chain stays scoped to the triggering runner (#470).

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006)."""
    from app.core.queue import queue

    queue.enqueue_in(
        timedelta(seconds=settings.BACKFILL_BATCH_PAUSE_SECONDS),
        backfill_streams_job,
        user_id,
    )


def _reschedule_after_budget(user_id: Optional[str] = None) -> None:
    """Re-enqueue this batch after the Strava-budget backoff (#544), when the
    shared budget is exhausted, so the chain resumes once capacity frees rather
    than dropping the runner's backfill."""
    from app.core.queue import queue

    queue.enqueue_in(
        timedelta(seconds=settings.STRAVA_BUDGET_BACKOFF_SECONDS),
        backfill_streams_job,
        user_id,
    )


def backfill_streams_job(user_id: Optional[str] = None) -> None:
    """RQ entrypoint. Runs one batch; if work remains, schedules the next.

    `user_id` scopes the eligible set to one runner (#470); the user-triggered
    path always supplies it, while an unscoped call still backfills globally.

    Yields to the shared Strava budget (#544/#601): the budget is checked before
    the batch AND before each activity inside it, so when the global budget is
    exhausted (up front or mid-batch) this defers (re-enqueues after the backoff)
    instead of calling Strava, leaving the shared ceiling for live webhook traffic.
    """
    if strava_budget.over_budget():
        _reschedule_after_budget(user_id)
        logger.info(
            "Backfill deferred: Strava budget exhausted; retrying in %ds",
            settings.STRAVA_BUDGET_BACKOFF_SECONDS,
        )
        return

    db = SessionLocal()
    try:
        result = asyncio.run(
            backfill_streams_batch(db, limit=settings.BACKFILL_BATCH_SIZE, user_id=user_id)
        )
    finally:
        db.close()

    if result.budget_exhausted:
        # Stopped mid-batch on the budget/rate-limit: resume after the budget
        # backoff (not the short batch pause), so we wait for capacity to free.
        _reschedule_after_budget(user_id)
        logger.info(
            "Backfill deferred mid-batch: Strava budget exhausted; retrying in %ds "
            "(%d remaining)",
            settings.STRAVA_BUDGET_BACKOFF_SECONDS,
            result.remaining,
        )
    elif result.remaining > 0:
        _schedule_next_batch(user_id)
        logger.info(
            "Backfill scheduled next batch in %ds (%d remaining)",
            settings.BACKFILL_BATCH_PAUSE_SECONDS,
            result.remaining,
        )
    else:
        logger.info("Backfill complete: no eligible activities remain")


def _acquire_enqueue_slot(user_id: str) -> bool:
    """Best-effort guard: at most one backfill chain enqueued per user per
    cooldown, so rapid re-triggers (a double Sync tap, or Sync racing the manual
    backfill endpoint) don't spawn overlapping chains that double-fetch the same
    activity's streams against the shared budget. Degrades OPEN on any Redis error
    so a coordination outage never blocks a legitimate backfill."""
    try:
        from app.core.queue import redis_conn

        return bool(
            redis_conn.set(
                f"backfill_enqueue:{user_id}",
                "1",
                nx=True,
                ex=_BACKFILL_ENQUEUE_COOLDOWN_S,
            )
        )
    except Exception as exc:  # Redis down / misconfigured: enqueue anyway
        logger.warning(
            "backfill_enqueue_guard_unavailable user=%s: %s; enqueuing anyway",
            user_id,
            exc,
        )
        return True


def enqueue_backfill(db: Session, user_id) -> int:
    """Count the runner's eligible activities and enqueue their first backfill batch.

    Returns the eligible count, scoped to `user_id` (#470). Now called on every
    `POST /api/sync` (#596), so a best-effort per-user cooldown collapses rapid
    re-triggers into one chain: `queue.enqueue`'s per-user `job_id` only dedups a
    still-queued run (not a chain already dequeued and self-rescheduling under a
    fresh id), so without the cooldown two near-simultaneous syncs could run two
    chains. The `streams_backfilled_at` marker makes any residual overlap
    idempotent, and the per-activity budget re-check (#601) bounds the cost."""
    user_id = str(user_id)
    eligible = count_eligible(db, user_id=user_id)
    if eligible and _acquire_enqueue_slot(user_id):
        queue.enqueue(
            backfill_streams_job,
            user_id,
            job_id=f"{_BACKFILL_JOB_ID}_{user_id}",
            result_ttl=3600,
        )
    return eligible
