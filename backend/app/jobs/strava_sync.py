import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models import User, StravaAccount, Activity
from app.services import activity_service

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
            print(f"Job failed: No Strava account for user_id {user_id}")
            return

        asyncio.run(activity_service.sync_recent_activities(db, account))
        print(f"Sync complete for user {user_id}")
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
            print(f"Skipping sync: Unknown athlete {strava_athlete_id}")
            return

        asyncio.run(
            activity_service.sync_activity_by_id(db, account, strava_activity_id)
        )
        print(f"Synced activity {strava_activity_id}")
    finally:
        db.close()
