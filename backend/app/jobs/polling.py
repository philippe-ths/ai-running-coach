"""Polling fallback for missed Strava webhooks.

Calls Strava's recent-activities list per linked account, diffs against the
local DB by `strava_activity_id`, and enqueues `process_new_activity_job`
for each previously-unseen id. Both webhook and polling converge on the
same pipeline; the dedup sentinel lives on `Activity.coach_notification_sent_at`.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import Activity, StravaAccount
from app.services.strava_ingestion import (
    StravaPort,
    ensure_valid_access_token,
    get_strava_port,
)

logger = logging.getLogger(__name__)

_LOOKBACK = timedelta(hours=6)


async def poll_for_new_activities(
    *, db: Session, strava_port: StravaPort
) -> list[int]:
    """For each linked StravaAccount, find newly-discovered activities and
    enqueue the pipeline. Returns the strava_activity_ids enqueued (for
    tests/visibility)."""
    accounts: Iterable[StravaAccount] = db.query(StravaAccount).all()
    enqueued: list[int] = []

    since = datetime.now(timezone.utc) - _LOOKBACK

    for account in accounts:
        try:
            access_token = await ensure_valid_access_token(db, account, strava_port)
            raw_activities = await strava_port.list_recent_activities(
                access_token=access_token, since=since, per_page=50
            )
        except Exception as exc:
            logger.warning(
                "Polling failed for athlete %s: %s",
                account.strava_athlete_id,
                exc,
            )
            continue

        strava_ids = [int(raw["id"]) for raw in raw_activities if "id" in raw]
        if not strava_ids:
            continue

        known = {
            row[0]
            for row in db.query(Activity.strava_activity_id)
            .filter(Activity.strava_activity_id.in_(strava_ids))
            .all()
        }
        new_ids = [sid for sid in strava_ids if sid not in known]

        for sid in new_ids:
            _enqueue_pipeline(account.strava_athlete_id, sid)
            enqueued.append(sid)

    return enqueued


def _enqueue_pipeline(athlete_id: int, activity_id: int) -> None:
    from app.jobs.process_new_activity import process_new_activity_job

    queue.enqueue(
        process_new_activity_job,
        strava_athlete_id=athlete_id,
        strava_activity_id=activity_id,
        job_id=f"pipeline_poll_{activity_id}",
        result_ttl=3600,
    )


def poll_for_new_activities_job() -> None:
    """RQ entrypoint. Opens its own DB session and uses the active Strava port."""
    db = SessionLocal()
    try:
        asyncio.run(
            poll_for_new_activities(db=db, strava_port=get_strava_port())
        )
    finally:
        db.close()
