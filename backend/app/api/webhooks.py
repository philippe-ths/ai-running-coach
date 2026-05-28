import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import get_db
from app.models import Activity
from app.core.queue import queue
from app.jobs.process_new_activity import process_new_activity_job
from app.jobs.strava_sync import sync_activity_job

logger = logging.getLogger(__name__)

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
    expected_token = settings.STRAVA_WEBHOOK_VERIFY_TOKEN
    if not expected_token and settings.APP_ENV == "production":
        # Fail closed: an empty configured token would otherwise let anyone
        # register a webhook subscription against this deployment by sending
        # ?hub.verify_token= against the default of "".
        logger.critical(
            "strava_webhook_verify_token_missing_in_production",
        )
        raise HTTPException(
            status_code=503,
            detail="Service unavailable: webhook verify token not configured",
        )

    if mode == "subscribe" and verify_token == expected_token:
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

    elif event.aspect_type == "create":
        # Fresh activity: run the full pipeline (ingest → analyze → coach → notify).
        job_id = f"pipeline_{event.object_id}_{event.event_time}"
        queue.enqueue(
            process_new_activity_job,
            strava_athlete_id=event.owner_id,
            strava_activity_id=event.object_id,
            job_id=job_id,
            result_ttl=3600,
        )
        return {"status": "processed", "action": "enqueued_pipeline"}

    elif event.aspect_type == "update":
        # Existing activity edit (title, visibility): re-ingest only.
        job_id = f"sync_{event.object_id}_{event.event_time}"
        queue.enqueue(
            sync_activity_job,
            strava_athlete_id=event.owner_id,
            strava_activity_id=event.object_id,
            job_id=job_id,
            result_ttl=3600,
        )
        return {"status": "processed", "action": "enqueued_sync"}

    return {"status": "ignored", "reason": "unknown_aspect"}
