import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import get_db
from app.models import Activity, StravaAccount
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

def _event_is_authentic(event: "StravaEvent", db: Session) -> bool:
    """Authenticate an incoming webhook event.

    Strava does not sign webhook payloads, and the endpoint is exempt from
    basic auth so the verification handshake can reach it. Anyone who learns
    the callback URL could otherwise POST a forged but well-formed event to
    enqueue jobs or soft-delete activities (see #100). Guard with two cheap
    equality checks against values we already hold:

    1. The event must reference our active push subscription. Strava only
       returns the subscription id to us at registration, so it is the
       stronger (secret-ish) check. Skipped when unconfigured (id 0) so local
       dev is not blocked.
    2. The event owner must be a connected athlete. Athlete ids are public, so
       this check is weaker on its own, but it is always available from the DB
       and never drifts, so it stays on even when (1) is unconfigured.
    """
    expected_subscription_id = settings.STRAVA_WEBHOOK_SUBSCRIPTION_ID
    if expected_subscription_id and event.subscription_id != expected_subscription_id:
        logger.warning(
            "strava_webhook_rejected_subscription_mismatch",
            extra={
                "event_subscription_id": event.subscription_id,
                "object_id": event.object_id,
            },
        )
        return False

    account = db.execute(
        select(StravaAccount).where(
            StravaAccount.strava_athlete_id == event.owner_id
        )
    ).scalars().first()
    if account is None:
        logger.warning(
            "strava_webhook_rejected_unknown_owner",
            extra={"owner_id": event.owner_id, "object_id": event.object_id},
        )
        return False

    return True


@router.post("/webhooks/strava")
async def receive_webhook(
    event: StravaEvent,
    db: Session = Depends(get_db)
):
    """
    Handle incoming events from Strava.
    """
    if not _event_is_authentic(event, db):
        # Reject before any side effect (enqueue or soft-delete). A forged
        # sender is not Strava, so a 403 here does not affect the real
        # subscription's health.
        raise HTTPException(status_code=403, detail="Unauthenticated webhook event")

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
