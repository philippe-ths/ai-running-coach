"""Shared self-pacing batch-chain mechanics for the maintenance jobs (#698).

`backfill_streams` (#110) and `reanalyze_history` (#300) both walk a large
eligible set in bounded batches, each batch scheduling its own successor via
RQ-native deferred scheduling so the single worker stays free for live traffic,
scoped per user (#470), never notifying. Only their eligibility predicate,
per-batch work, and mark/advance strategy differ (backfill marks a DB flag and is
rate-limit driven; reanalyze walks a `strava_activity_id` cursor with no Strava
calls). This module owns the parts that do NOT differ, so pacing, idempotency,
and cooldown behaviour are fixed in one place instead of two (or three):

* the eligible-set query fragments both eligibility builders share
  (`has_streams_exists`, `scope_to_user`) and the `count_eligible` mechanic;
* the per-user enqueue-cooldown idiom (`acquire_enqueue_slot`), also used by the
  self-heal trigger (#123);
* the deferred successor scheduling (`schedule_after`).

The mark/advance strategy stays in each job: it is expressed as the job's own
eligibility filter (a `streams_backfilled_at` marker vs a cursor bound) plus how
its entrypoint threads args into `schedule_after`.
"""

import logging
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, ActivityStream

logger = logging.getLogger(__name__)

# result_ttl on the first-batch enqueue: keep the finished job result briefly so a
# rapid re-trigger sees the per-user job id, then let it expire.
CHAIN_RESULT_TTL = 3600


def has_streams_exists():
    """EXISTS(...) clause, true when the activity has stored ``ActivityStream`` rows.

    Reanalyze REQUIRES it (only stored streams can be re-derived offline, no Strava
    call); backfill NEGATES it (``~has_streams_exists()`` selects the summary-only
    activities that still need streams fetched). One construction, opposite polarity.
    """
    return (
        select(ActivityStream.id)
        .where(ActivityStream.activity_id == Activity.id)
        .exists()
    )


def scope_to_user(stmt, user_id: Optional[str]):
    """Restrict an ``Activity`` query to one runner (#470); a ``None`` user is a global pass.

    ``user_id`` rides through RQ as a string, so coerce it to ``UUID`` for the Uuid
    column comparison to work on both Postgres and the SQLite test backend.
    """
    if user_id is None:
        return stmt
    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)
    return stmt.where(Activity.user_id == user_id)


def count_eligible(db: Session, stmt) -> int:
    """How many rows an eligibility statement selects."""
    return len(db.execute(stmt).scalars().all())


def acquire_enqueue_slot(
    key: str, cooldown_seconds: int, *, degrade_open: bool = True
) -> bool:
    """Best-effort per-user cooldown: at most one chain enqueued per ``key`` per
    ``cooldown_seconds`` via an atomic Redis ``SET NX EX``, so rapid re-triggers (a
    double Sync tap, app-open spam) collapse into one chain rather than spawning
    overlapping chains against the shared budget.

    ``degrade_open`` picks the Redis-outage fallback: ``True`` enqueues anyway (a
    coordination outage must never block a legitimate maintenance run — the job's
    own marker/idempotency bounds any residual overlap), ``False`` skips (a
    best-effort safety net can wait for the next trigger). The idiom lives here
    once (#698); backfill (#601) and self-heal (#123) both call it.
    """
    try:
        from app.core.queue import redis_conn

        return bool(redis_conn.set(key, "1", nx=True, ex=cooldown_seconds))
    except Exception as exc:  # noqa: BLE001 — Redis down / misconfigured
        logger.warning(
            "enqueue_cooldown_unavailable key=%s degrade_open=%s: %s; proceeding accordingly",
            key,
            degrade_open,
            exc,
        )
        return degrade_open


def schedule_after(job_func, pause_seconds: int, *args) -> None:
    """Schedule the chain's successor after ``pause_seconds`` so the single worker
    stays free to process webhooks between batches. Carries ``*args`` (cursor and/or
    user) so the chain stays scoped to the triggering runner and resumes where it
    left off.

    Uses RQ-native deferred scheduling (drained by the worker's ``with_scheduler``),
    so no separate rq-scheduler process is needed (#123/ADR 0006).
    """
    from app.core.queue import queue

    queue.enqueue_in(timedelta(seconds=pause_seconds), job_func, *args)
