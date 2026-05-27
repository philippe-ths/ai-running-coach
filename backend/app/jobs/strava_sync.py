import asyncio
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import StravaAccount
from app.services import analysis
from app.services.strava_ingestion import (
    get_strava_port,
    ingest_activity_by_id,
    ingest_recent_activities,
)

logger = logging.getLogger(__name__)


def sync_recent_activities_job(user_id: str):
    """RQ Job: Sync recent activities for a user, then analyze each."""
    db = SessionLocal()
    try:
        stmt = select(StravaAccount).where(StravaAccount.user_id == user_id)
        account = db.execute(stmt).scalars().first()

        if not account:
            logger.error("Job failed: No Strava account for user_id %s", user_id)
            return

        activities, _stats = asyncio.run(
            ingest_recent_activities(db, account, get_strava_port())
        )
        for activity in activities:
            try:
                analysis.analyze(db, str(activity.id))
            except Exception as exc:
                logger.error(
                    "Analysis failed for activity %s: %s",
                    activity.strava_activity_id,
                    exc,
                )
        logger.info("Sync complete for user %s", user_id)
    finally:
        db.close()


def sync_activity_job(strava_athlete_id: int, strava_activity_id: int):
    """RQ Job: Ingest a single activity by Strava id. No analysis."""
    db = SessionLocal()
    try:
        stmt = select(StravaAccount).where(
            StravaAccount.strava_athlete_id == strava_athlete_id
        )
        account = db.execute(stmt).scalars().first()

        if not account:
            logger.warning("Skipping sync: Unknown athlete %s", strava_athlete_id)
            return

        asyncio.run(
            ingest_activity_by_id(db, account, get_strava_port(), strava_activity_id)
        )
        logger.info("Synced activity %s", strava_activity_id)
    finally:
        db.close()
