"""Bulk re-analysis of already-analyzed history (#300).

A manually-triggered, self-pacing job. It re-runs the analysis pipeline from
streams ALREADY stored in the database (no Strava calls), so a change to how
analysis derives a stored signal (e.g. #297, which bins time-in-zone against the
runner's Strava zones) propagates to historical `DerivedMetric` rows instead of
only reaching newly-synced activities. It never notifies.

An activity is eligible when it is not soft-deleted and has stored
`ActivityStream` rows. Re-analysis is idempotent — `analyze` upserts the
`DerivedMetric` — so re-running is always safe.

Pagination uses a `strava_activity_id` cursor carried in the job args (that
column is unique and roughly time-ordered): each batch processes the next chunk
of eligible activities older than the cursor (newest first) and, while work
remains, schedules its successor via rq-scheduler `enqueue_in` carrying the
advanced cursor. A fresh trigger starts with no cursor (from the newest
activity), which is exactly the "re-run after a derivation change" semantics — no
DB marker and no migration are needed. A worker crash mid-run is recovered by
re-triggering: the chain restarts from the newest activity and idempotently
re-derives until it passes where it stopped.

Unlike the stream backfill (#110) the pacing is not rate-limit driven — there are
no Strava calls — so the batch is large and the pause is short; it just yields
the single worker to webhooks/self-heal between batches.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.jobs import batch_chain
from app.models import Activity, ActivityStream
from app.services.analysis import analyze
from app.services.analysis.baseline import recompute_runner_baseline

logger = logging.getLogger(__name__)

_REANALYZE_JOB_ID = "reanalyze_history"


def _eligible_stmt(*, cursor: Optional[int] = None, user_id: Optional[str] = None):
    """Non-deleted activities that have stored streams, newest first.

    Only activities with stored streams can be re-derived offline (no Strava
    call). When `cursor` is given, restrict to activities strictly older than it
    by `strava_activity_id`, so a batch chain walks the whole set exactly once
    without gaps or repeats (the column is unique). When `user_id` is given the
    set is scoped to that runner (#470), so a user's trigger only re-analyzes
    their own history; omit it for a global pass (the offline eval rebuild).
    """
    stmt = select(Activity).where(
        Activity.is_deleted.is_(False),
        batch_chain.has_streams_exists(),
    )
    if cursor is not None:
        stmt = stmt.where(Activity.strava_activity_id < cursor)
    stmt = batch_chain.scope_to_user(stmt, user_id)
    return stmt.order_by(Activity.strava_activity_id.desc())


def count_eligible(
    db: Session, *, cursor: Optional[int] = None, user_id: Optional[str] = None
) -> int:
    """How many activities are still eligible (optionally past the cursor / scoped)."""
    return batch_chain.count_eligible(
        db, _eligible_stmt(cursor=cursor, user_id=user_id)
    )


def reanalyze_activity_if_streams(db: Session, activity_id) -> bool:
    """Re-derive ONE activity's analysis from its ALREADY-STORED streams (no Strava
    call) — the single-activity form of the bulk re-analysis (#646).

    The coach-report "Re-run" calls this before regenerating so the report reflects
    the current deployed analysis code instead of a stale/buggy `DerivedMetric` frozen
    at the activity's original analysis. `analyze` upserts the `DerivedMetric` and
    commits, so it is idempotent and safe to repeat.

    Returns True when analysis ran, False when it was skipped because the activity has
    no stored streams (a summary-only import) — the caller then proceeds with the
    existing metrics rather than failing or reaching out to Strava.
    """
    activity_uuid = (
        activity_id if isinstance(activity_id, uuid.UUID) else uuid.UUID(str(activity_id))
    )
    has_streams = (
        db.query(ActivityStream.id)
        .filter(ActivityStream.activity_id == activity_uuid)
        .first()
        is not None
    )
    if not has_streams:
        return False
    analyze(db, str(activity_uuid))
    return True


@dataclass
class ReanalyzeBatchResult:
    processed: list[int] = field(default_factory=list)  # strava_activity_ids
    next_cursor: Optional[int] = None  # None => drained, no further batch
    remaining: int = 0


def reanalyze_history_batch(
    db: Session, *, limit: int, cursor: Optional[int] = None, user_id: Optional[str] = None
) -> ReanalyzeBatchResult:
    """Re-analyze up to `limit` eligible activities older than `cursor`.

    `analyze` re-derives each `DerivedMetric` from the stored streams and commits
    per activity, so progress survives an interruption. A failure on one activity
    is logged and skipped (convergence over completeness); the session is rolled
    back so the next activity starts clean. Scoped to `user_id` when given (#470).
    Never notifies.
    """
    batch = db.execute(
        _eligible_stmt(cursor=cursor, user_id=user_id).limit(limit)
    ).scalars().all()
    # Snapshot identity up front: a per-activity failure rolls the session back,
    # which expires the ORM instances in `batch`, so re-reading their attributes
    # afterwards would re-query (or raise on a now-absent row). The id, strava
    # id, and user id are all we need downstream.
    items = [
        (activity.id, activity.strava_activity_id, activity.user_id)
        for activity in batch
    ]
    result = ReanalyzeBatchResult()

    # Defer the runner-baseline recompute to once per touched user after the
    # batch (#366): analyze() recomputes it per call by default, which redid the
    # 182-day windowed scan up to REANALYZE_BATCH_SIZE times per batch. We pass
    # skip_baseline=True here and recompute once per user below; the recompute
    # is a full idempotent pass, so the final baseline is identical.
    # NB: the loop variables below are deliberately NOT named `user_id` — that is
    # this function's owner-scoping parameter, and shadowing it made the
    # `count_eligible` call at the end of the batch read the LAST activity's owner
    # instead of the requested scope, so a global pass under-reported `remaining`
    # (#804).
    recompute_user_ids: list = []
    for activity_id, strava_id, owner_id in items:
        try:
            analyze(db, str(activity_id), skip_baseline=True)
        except Exception as exc:  # noqa: BLE001 - convergence over completeness
            db.rollback()
            logger.error(
                "Re-analysis failed for activity %s (strava id %s): %s",
                activity_id,
                strava_id,
                exc,
            )
        else:
            if owner_id not in recompute_user_ids:
                recompute_user_ids.append(owner_id)
        result.processed.append(strava_id)

    # One baseline recompute per user that had at least one activity succeed.
    # Best-effort, mirroring analyze()'s _post_commit_baseline: a failure is
    # logged and the session rolled back so the next user starts clean, never
    # aborting the chain.
    for owner_id in recompute_user_ids:
        try:
            recompute_runner_baseline(db, owner_id)
        except Exception:  # noqa: BLE001 - baseline is best-effort
            logger.exception("runner baseline recompute failed for user %s", owner_id)
            db.rollback()

    # A full batch means there may be more; a short batch means we drained the
    # set. Advance the cursor to the last id seen so the next batch continues
    # strictly past it.
    if len(items) == limit and items:
        result.next_cursor = items[-1][1]
        result.remaining = count_eligible(db, cursor=result.next_cursor, user_id=user_id)

    logger.info(
        "Re-analysis batch processed %d activities, %d remaining",
        len(result.processed),
        result.remaining,
    )
    return result


def _schedule_next_batch(cursor: int, user_id: Optional[str] = None) -> None:
    """Schedule the next batch after the configured pause. Carries the advanced
    `cursor` and `user_id` so the self-paced chain resumes strictly past where it
    stopped and stays scoped to the triggering runner (#470). The chain mechanics
    live in `batch_chain.schedule_after` (#698)."""
    batch_chain.schedule_after(
        reanalyze_history_job, settings.REANALYZE_BATCH_PAUSE_SECONDS, cursor, user_id
    )


def reanalyze_history_job(
    cursor: Optional[int] = None, user_id: Optional[str] = None
) -> None:
    """RQ entrypoint. Runs one batch; if work remains, schedules the next.

    `user_id` scopes the eligible set to one runner (#470); the user-triggered
    path always supplies it, while an unscoped call still re-analyzes globally.
    """
    db = SessionLocal()
    try:
        result = reanalyze_history_batch(
            db, limit=settings.REANALYZE_BATCH_SIZE, cursor=cursor, user_id=user_id
        )
    finally:
        db.close()

    if result.next_cursor is not None:
        _schedule_next_batch(result.next_cursor, user_id)
        logger.info(
            "Re-analysis scheduled next batch in %ds (%d remaining)",
            settings.REANALYZE_BATCH_PAUSE_SECONDS,
            result.remaining,
        )
    else:
        logger.info("Re-analysis complete: no eligible activities remain")


def enqueue_reanalyze(db: Session, user_id) -> int:
    """Count the runner's eligible activities and enqueue their first re-analysis batch.

    Returns the eligible count, scoped to `user_id` (#470). A per-user job id
    keeps a re-trigger from starting a second chain for the same runner while one
    is in flight, without colliding with another runner's chain; re-analysis is
    idempotent regardless.
    """
    user_id = str(user_id)
    eligible = count_eligible(db, user_id=user_id)
    if eligible:
        queue.enqueue(
            reanalyze_history_job,
            None,  # cursor: start from the newest activity
            user_id,
            job_id=f"{_REANALYZE_JOB_ID}_{user_id}",
            result_ttl=batch_chain.CHAIN_RESULT_TTL,
        )
    return eligible
