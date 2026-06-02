"""Pipeline job that runs ingest → analyze → coach report → notify for a fresh activity.

Both the Strava webhook (`aspect_type=create`) and the polling fallback enqueue
this job. Dedup is enforced via `Activity.coach_notification_sent_at`.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Activity, StravaAccount
from app.models.coach_report import CoachReport
from app.services.analysis import analyze_with_streams
from app.services.coach.service import get_or_generate_coach_report
from app.services.notifications import (
    Notification,
    build_coach_notification,
    get_notifier,
)
from app.services.notifications.port import NotifierPort
from app.services.strava_ingestion import (
    StravaPort,
    get_strava_port,
    ingest_activity_by_id,
)

logger = logging.getLogger(__name__)


async def process_new_activity(
    *,
    db: Session,
    account: StravaAccount,
    strava_activity_id: int,
    strava_port: StravaPort,
    notifier: NotifierPort,
) -> Optional[Notification]:
    """Run the full ingest → analyze → coach → notify pipeline.

    Returns the Notification that was sent, or None if skipped (already
    notified, fallback report, notifications unconfigured, send failed, or
    missing inputs).
    """
    activity = await ingest_activity_by_id(
        db, account, strava_port, strava_activity_id
    )

    await analyze_with_streams(db, str(activity.id))

    report = await get_or_generate_coach_report(db, str(activity.id))
    if report is None:
        logger.info(
            "Skipping notification for activity %s: no coach report",
            strava_activity_id,
        )
        return None

    db.refresh(activity)
    coach_row = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == _coerce_uuid(activity.id))
        .first()
    )
    if coach_row is None or coach_row.is_fallback:
        logger.info(
            "Skipping notification for activity %s: report is fallback or missing",
            strava_activity_id,
        )
        return None

    if activity.coach_notification_sent_at is not None:
        logger.info(
            "Skipping notification for activity %s: already notified at %s",
            strava_activity_id,
            activity.coach_notification_sent_at,
        )
        return None

    activity_class = (
        activity.metrics.activity_class if activity.metrics else (activity.type or "Activity")
    )
    notification = build_coach_notification(
        report=report,
        activity_class=activity_class,
        distance_m=activity.distance_m or 0,
        app_base_url=settings.APP_BASE_URL,
    )
    if notification is None:
        logger.info(
            "Skipping notification for activity %s: no channel configured",
            strava_activity_id,
        )
        return None

    try:
        notifier.send(notification)
    except Exception:
        # A delivery failure must not crash the pipeline or lose the coach
        # report (already persisted). Leave the sentinel null so the activity
        # remains eligible for a later re-send (e.g. the manual action in #114),
        # and log loudly so the failure is observable rather than silent.
        logger.exception(
            "Notification send failed for activity %s; sentinel left unset",
            strava_activity_id,
        )
        return None

    activity.coach_notification_sent_at = datetime.now(timezone.utc)
    db.add(activity)
    db.commit()

    return notification


def process_new_activity_job(
    strava_athlete_id: int, strava_activity_id: int
) -> None:
    """RQ entrypoint. Opens its own DB session and uses the active ports."""
    db = SessionLocal()
    try:
        account = (
            db.query(StravaAccount)
            .filter(StravaAccount.strava_athlete_id == strava_athlete_id)
            .first()
        )
        if account is None:
            logger.warning(
                "process_new_activity_job: unknown athlete %s", strava_athlete_id
            )
            return

        asyncio.run(
            process_new_activity(
                db=db,
                account=account,
                strava_activity_id=strava_activity_id,
                strava_port=get_strava_port(),
                notifier=get_notifier(),
            )
        )
    finally:
        db.close()


def _coerce_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
