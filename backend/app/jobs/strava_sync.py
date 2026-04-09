import asyncio
import logging
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models import User, StravaAccount, Activity
from app.services import activity_service

logger = logging.getLogger(__name__)

def sync_recent_activities_job(user_id: str):
    """
    RQ Job: Sync recent activities for a user.
    """
    db = SessionLocal()
    try:
        # Resolve StravaAccount
        stmt = select(StravaAccount).where(StravaAccount.user_id == user_id)
        account = db.execute(stmt).scalars().first()
        
        if not account:
            logger.error("Job failed: No Strava account for user_id %s", user_id)
            return

        asyncio.run(activity_service.sync_recent_activities(db, account))
        logger.info("Sync complete for user %s", user_id)
    finally:
        db.close()

def sync_activity_job(strava_athlete_id: int, strava_activity_id: int):
    """
    RQ Job: Sync specific activity.
    """
    db = SessionLocal()
    try:
        stmt = select(StravaAccount).where(StravaAccount.strava_athlete_id == strava_athlete_id)
        account = db.execute(stmt).scalars().first()
        
        if not account:
            logger.warning("Skipping sync: Unknown athlete %s", strava_athlete_id)
            return

        asyncio.run(
            activity_service.sync_activity_by_id(db, account, strava_activity_id)
        )
        logger.info("Synced activity %s", strava_activity_id)
    finally:
        db.close()
