from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import get_db
from app.models import Activity
from app.core.queue import queue
from app.jobs.strava_sync import sync_activity_job

router = APIRouter()

# Schema for the incoming webhook event
# https://developers.strava.com/docs/webhooks/
class StravaEvent(BaseModel):
    object_type: str  # activity, athlete
    object_id: int    # activity ID or athlete ID
    aspect_type: str  # create, update, delete
    owner_id: int     # athlete ID
    subscription_id: int
    updates: dict = {} # e.g. title changes
    event_time: int

@router.get("/webhooks/strava")
def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    verify_token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge")
):
    """
    Strava verification challenge.
    """
    if mode == "subscribe" and verify_token == settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
        return {"hub.challenge": challenge}
    
    raise HTTPException(status_code=403, detail="Invalid verification token")

@router.post("/webhooks/strava")
async def receive_webhook(
    event: StravaEvent, 
    db: Session = Depends(get_db)
):
    """
    Handle incoming events from Strava.
    """
    if event.object_type != "activity":
        # We assume we only care about activities for now
        return {"status": "ignored", "reason": "not_activity"}

    if event.aspect_type == "delete":
        # Soft delete
        stmt = update(Activity).where(
            Activity.strava_activity_id == event.object_id
        ).values(is_deleted=True)
        db.execute(stmt)
        db.commit()
        return {"status": "processed", "action": "deleted"}

    elif event.aspect_type in ["create", "update"]:
        # Enqueue job
        # We use explicit job ID to prevent dupes in queue
        job_id = f"sync_{event.object_id}_{event.event_time}"
        
        queue.enqueue(
            sync_activity_job, 
            strava_athlete_id=event.owner_id, 
            strava_activity_id=event.object_id,
            job_id=job_id,
            result_ttl=3600 # Keep result for 1 hour
        )
        
        return {"status": "processed", "action": "enqueued"}

    return {"status": "ignored", "reason": "unknown_aspect"}
