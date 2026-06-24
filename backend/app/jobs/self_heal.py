"""User-triggered self-healing for missed Strava webhooks (#123, ADR 0006).

Replaces the time-based polling fallback, which could not survive multi-user:
Strava's rate limit is per-application, so steady-state polling across many users
exhausts the shared budget. Instead, an authenticated app-open (or an explicit
Refresh tap) enqueues ONE per-user check: fetch the user's recent Strava
activities (one API call), diff against what is already stored for that user, and
enqueue ``process_new_activity_job`` for anything the webhook missed. This costs
zero when nobody is using the app.

The check is bounded (``maybe_enqueue_self_heal``) to at most one run per user per
``SELF_HEAL_MIN_INTERVAL_SECONDS`` via an atomic Redis key, so rapid refreshes do
not multiply Strava calls. The existing per-activity dedup
(``Activity.coach_notification_sent_at`` + the deterministic ``job_id``) still
guarantees an activity is processed once even if the webhook and the self-heal
race.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.queue import queue, redis_conn
from app.db.session import SessionLocal
from app.models import Activity, StravaAccount
from app.services.strava_ingestion import (
    ensure_valid_access_token,
    get_strava_port,
)

logger = logging.getLogger(__name__)

# How far back the self-heal looks for activities the webhook missed. A bounded
# recent window keeps the fetch to a single Strava page for a recreational runner
# (the same recovery horizon the removed polling fallback used); a gap older than
# this is closed by a manual POST /api/sync, not the self-heal safety net.
_LOOKBACK = timedelta(days=7)
_PER_PAGE = 30


def maybe_enqueue_self_heal(user_id) -> bool:
    """Enqueue a self-heal check for ``user_id`` unless one ran very recently.

    Bounded by an atomic Redis SET NX EX, so app-open spam and rapid Refresh taps
    collapse to at most one check per ``SELF_HEAL_MIN_INTERVAL_SECONDS``. Returns
    True when a check was enqueued, False when throttled or on any error — it is a
    best-effort safety net and must never break the request that triggered it.
    """
    try:
        key = f"self_heal:{user_id}"
        acquired = redis_conn.set(
            key, "1", nx=True, ex=settings.SELF_HEAL_MIN_INTERVAL_SECONDS
        )
        if not acquired:
            return False
        queue.enqueue(self_heal_job, str(user_id), job_id=f"self_heal_{user_id}")
        return True
    except Exception as exc:  # noqa: BLE001 — never propagate into the request
        logger.warning("self_heal enqueue failed for user %s: %s", user_id, exc)
        return False


async def _run_self_heal(*, db, user_id) -> list[int]:
    account = (
        db.query(StravaAccount).filter(StravaAccount.user_id == user_id).first()
    )
    if account is None:
        return []  # no linked Strava account — nothing to heal

    strava_port = get_strava_port()
    since = datetime.now(timezone.utc) - _LOOKBACK
    try:
        access_token = await ensure_valid_access_token(db, account, strava_port)
        raw_activities = await strava_port.list_recent_activities(
            access_token=access_token, since=since, per_page=_PER_PAGE
        )
    except Exception as exc:  # noqa: BLE001 — Strava down/token bad: log and skip
        logger.warning(
            "self_heal Strava fetch failed for athlete %s: %s",
            account.strava_athlete_id,
            exc,
        )
        return []

    strava_ids = [int(raw["id"]) for raw in raw_activities if "id" in raw]
    if not strava_ids:
        return []

    # Diff against THIS user's stored activities only (multi-user safe).
    known = {
        row[0]
        for row in db.query(Activity.strava_activity_id)
        .filter(
            Activity.user_id == user_id,
            Activity.strava_activity_id.in_(strava_ids),
        )
        .all()
    }
    new_ids = [sid for sid in strava_ids if sid not in known]

    from app.jobs.process_new_activity import PIPELINE_RETRY, process_new_activity_job

    for sid in new_ids:
        queue.enqueue(
            process_new_activity_job,
            strava_athlete_id=account.strava_athlete_id,
            strava_activity_id=sid,
            job_id=f"pipeline_self_heal_{sid}",
            result_ttl=3600,
            retry=PIPELINE_RETRY,
        )
    if new_ids:
        logger.info(
            "self_heal enqueued %d missed activities for user %s", len(new_ids), user_id
        )
    return new_ids


def self_heal_job(user_id: str) -> None:
    """RQ entrypoint. Opens its own DB session and runs the per-user check."""
    db = SessionLocal()
    try:
        asyncio.run(_run_self_heal(db=db, user_id=user_id))
    finally:
        db.close()
