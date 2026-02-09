from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select
from fastapi import HTTPException

from app.models import Activity, StravaAccount, User, ActivityStream, DerivedMetric
from app.services.strava.client import strava_client
from app.schemas import SyncResponse

logger = logging.getLogger(__name__)

async def fetch_and_store_streams(db: Session, strava_account: StravaAccount, activity: Activity) -> bool:
    """
    Fetches streams from Strava and stores them. Returns True if successful.
    """
    # Desired stream types for analysis
    stream_types = [
        "time", "distance", "latlng", "altitude", "velocity_smooth", 
        "heartrate", "cadence", "watts", "temp", "moving", "grade_smooth"
    ]
    
    token = await strava_client.ensure_valid_token(db, strava_account)
    streams_data = await strava_client.get_activity_streams(token, activity.strava_activity_id, stream_types)
    
    if not streams_data:
        return False

    # Check existing to avoid duplication (simple delete/replace for MVP deep analysis)
    db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).delete()

    for s_type, s_obj in streams_data.items():
        # s_obj format from Strava: {'original_size': N, 'resolution': 'high', 'series_type': 'distance', 'data': [...]}
        new_stream = ActivityStream(
            activity_id=activity.id,
            stream_type=s_type,
            data=s_obj.get("data", [])
        )
        db.add(new_stream)
    
    db.commit()
    return True

def upsert_activity(db: Session, raw: dict, user_id: str) -> Activity:
    """
    Parses raw Strava JSON and updates or inserts Activity.
    """
    stmt = select(Activity).where(Activity.strava_activity_id == raw["id"])
    existing = db.execute(stmt).scalars().first()
    
    # Parse fields
    activity_data = {
        "user_id": user_id,
        "strava_activity_id": raw["id"],
        "name": raw.get("name", "Unknown Run"),
        "type": raw.get("type", "Run"),
        "start_date": datetime.strptime(raw["start_date"], "%Y-%m-%dT%H:%M:%SZ"),
        "distance_m": int(raw.get("distance", 0)),
        "moving_time_s": raw.get("moving_time", 0),
        "elapsed_time_s": raw.get("elapsed_time", 0),
        "elev_gain_m": raw.get("total_elevation_gain", 0.0),
        "avg_hr": raw.get("average_heartrate"),
        "max_hr": raw.get("max_heartrate"),
        "avg_cadence": raw.get("average_cadence"),
        "average_speed_mps": raw.get("average_speed"),
        "raw_summary": raw
    }

    if existing:
        for key, value in activity_data.items():
            setattr(existing, key, value)
        db.add(existing)
        return existing
    else:
        new_activity = Activity(**activity_data)
        db.add(new_activity)
        return new_activity

async def sync_recent_activities(db: Session, strava_account: StravaAccount) -> SyncResponse:
    """
    Fetches last 30 days of activities, upserts them, runs analysis,
    and returns detailed stats.
    """
    stats = SyncResponse()
    
    try:
        # ensure token is valid
        token = await strava_client.ensure_valid_token(db, strava_account)
        
        # 30 days ago
        thirty_days_ago = int((datetime.now() - timedelta(days=30)).timestamp())
        
        # Fetch from Strava
        raw_activities = await strava_client.get_athlete_activities(
            access_token=token,
            after=thirty_days_ago,
            per_page=50
        )
        
        stats.fetched = len(raw_activities)
        
        for raw in raw_activities:
            try:
                # 1. Upsert Activity
                activity = upsert_activity(db, raw, strava_account.user_id)
                db.flush() # Ensure ID is populated
                stats.upserted += 1
                
                # 1.5 Fetch Streams
                await fetch_and_store_streams(db, strava_account, activity)

                # 2. Trigger Analysis (Idempotent-ish: re-running updates flags/metrics)
                # Optimization: Skip analysis if already done to prevent hanging on sync
                # Dynamic import to avoid circular dependency
                from app.services.analysis import engine as analysis_engine
                
                # Check for existing
                existing_metrics = db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity.id).first()
                
                metrics = existing_metrics
                if not existing_metrics:
                    metrics = analysis_engine.run_analysis(db, activity.id)
                    stats.analyzed += 1
                
                # Commit per activity to allow partial success
                db.commit()
                
            except Exception as e:
                db.rollback()
                msg = f"Error processing activity {raw.get('id')}: {str(e)}"
                logger.error(msg)
                stats.errors.append(msg)

    except Exception as e:
        msg = f"Sync failed globally: {str(e)}"
        logger.error(msg)
        stats.errors.append(msg)
    
    return stats

async def sync_activity_by_id(db: Session, strava_account: StravaAccount, strava_activity_id: int):
    """
    Fetches a specific activity by ID.
    """
    token = await strava_client.ensure_valid_token(db, strava_account)
    
    raw = await strava_client.get_activity(token, strava_activity_id)
    upsert_activity(db, raw, strava_account.user_id)
    
    db.commit()
    return {"synced": 1, "id": strava_activity_id}

def get_activities(db: Session, skip: int = 0, limit: int = 20):
    return db.query(Activity).order_by(Activity.start_date.desc()).offset(skip).limit(limit).all()

def get_activity(db: Session, activity_id: str):
    return db.query(Activity).options(
        joinedload(Activity.metrics),
        joinedload(Activity.check_in),
        joinedload(Activity.streams)
    ).filter(Activity.id == activity_id).first()
