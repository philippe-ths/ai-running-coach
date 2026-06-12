from datetime import datetime, timedelta
from typing import Annotated, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.models import Activity, StravaAccount, CheckIn
from app.schemas import ActivityRead, ActivityDetailRead, CheckInCreate, CheckInRead, SyncResponse, ActivityIntentUpdate, DerivedMetricRead
from app.services import activity_queries, analysis
from app.services.checkins import write_checkin
from app.services.analysis.classifier import Classification, compose_headline
from app.services.analysis.splits import calculate_splits
from app.services.laps import project_laps
from app.services.strava_ingestion import get_strava_port, ingest_recent_activities

router = APIRouter()

@router.post("/activities/{activity_id}/process_deep", response_model=DerivedMetricRead)
async def process_activity_deep(
    activity_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Fetches full streams from Strava (if Rate Limits allow) and re-runs processing.
    Useful for detailed breakdown of 'Complex' runs.
    """
    metrics = await analysis.analyze_with_streams(db, str(activity_id))
    if not metrics:
        raise HTTPException(status_code=400, detail="Processing failed or activity not found.")

    read = DerivedMetricRead.model_validate(metrics)
    read.headline = compose_headline(metrics.activity, Classification.from_metrics(metrics))
    return read

@router.post("/activities/backfill-streams")
def backfill_streams(db: Session = Depends(get_db)):
    """Kick off a paced backfill of stream-derived analysis for historical
    summary-only activities (#110).

    Enqueues a self-pacing job that fetches streams and re-runs analysis a small
    batch at a time, staying within Strava's rate limits and never notifying.
    Returns how many activities are currently eligible.
    """
    from app.jobs.backfill_streams import enqueue_backfill

    eligible = enqueue_backfill(db)
    return {
        "eligible": eligible,
        "status": "scheduled" if eligible else "nothing_to_do",
    }


@router.put("/activities/{activity_id}/intent", response_model=ActivityRead)
def update_activity_intent(
    activity_id: UUID,
    payload: ActivityIntentUpdate,
    db: Session = Depends(get_db)
):
    """
    Updates the manual user intent for an activity and re-runs analysis.
    """
    stmt = select(Activity).where(Activity.id == activity_id)
    activity = db.execute(stmt).scalars().first()
    
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
        
    activity.user_intent = payload.user_intent
    db.add(activity)
    db.commit()
    db.refresh(activity)
    
    # Re-run processing pipeline with new intent
    analysis.analyze(db, str(activity_id))
    
    return activity

# Sync windows up to this many days fetch streams eagerly (full deep
# processing). Beyond it, sync is treated as a historical backfill and imports
# summaries only, because one stream call per activity over a long window would
# exceed Strava's 100-requests/15-min limit. See #109.
_STREAM_FETCH_WINDOW_DAYS = 30


@router.post("/sync", response_model=SyncResponse)
async def sync_activities(
    # In a real app, we'd get current_user from token.
    # Here, we optionally take an ID or default to the first account found.
    strava_athlete_id: Optional[int] = None,
    since_days: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "How many days back to sync. Windows up to "
                f"{_STREAM_FETCH_WINDOW_DAYS} days fetch full streams; larger "
                "windows import activity summaries only (historical backfill)."
            ),
        ),
    ] = _STREAM_FETCH_WINDOW_DAYS,
    db: Session = Depends(get_db)
):
    """
    Triggers a manual sync of the last `since_days` days of activities.

    The default window fetches streams and is the routine sync. A larger window
    backfills summaries only (streams cost one Strava call each and would breach
    the rate limit over a long window); analysis still runs from the summary.
    """
    if strava_athlete_id:
        stmt = select(StravaAccount).where(StravaAccount.strava_athlete_id == strava_athlete_id)
        account = db.execute(stmt).scalars().first()
    else:
        # Default: take the first account (Single Player Mode)
        account = db.query(StravaAccount).first()

    if not account:
        raise HTTPException(status_code=404, detail="No linked Strava account found. Connect Strava first.")

    since = datetime.now() - timedelta(days=since_days)
    fetch_streams = since_days <= _STREAM_FETCH_WINDOW_DAYS
    activities, stats = await ingest_recent_activities(
        db, account, get_strava_port(), since=since, fetch_streams=fetch_streams
    )

    for activity in activities:
        try:
            analysis.analyze(db, str(activity.id))
            stats.analyzed += 1
        except Exception as exc:
            stats.errors.append(
                f"Analysis failed for activity {activity.strava_activity_id}: {exc}"
            )

    return stats

@router.get("/activities", response_model=List[ActivityRead])
def read_activities(
    skip: int = 0, 
    limit: int = 20, 
    db: Session = Depends(get_db)
):
    """
    Get stored activities (paginated).
    """
    # Note: In multi-user app, filter by current_user.id
    activities = activity_queries.get_activities(db, skip=skip, limit=limit)
    responses = []
    for activity in activities:
        response = ActivityRead.model_validate(activity)
        if activity.metrics:
            response.headline = compose_headline(
                activity, Classification.from_metrics(activity.metrics)
            )
        responses.append(response)
    return responses

@router.get("/activities/{activity_id}", response_model=ActivityDetailRead)
def read_activity(
    activity_id: UUID, 
    db: Session = Depends(get_db)
):
    activity = activity_queries.get_activity(db, str(activity_id))
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
        
    # Calculate Splits
    effective_type = activity.user_intent if activity.user_intent else activity.type
    splits_data = calculate_splits(
        activity.streams or [], activity_type=effective_type
    )

    # Convert to Pydantic model manually to inject transient splits + headline
    response = ActivityDetailRead.model_validate(activity)
    response.splits = splits_data
    response.laps = project_laps(activity.raw_summary, effective_type)
    if response.metrics:
        response.metrics.headline = compose_headline(
            activity, Classification.from_metrics(activity.metrics)
        )

    return response

@router.post("/activities/{activity_id}/checkin", response_model=CheckInRead)
def create_checkin(
    activity_id: UUID,
    checkin_data: CheckInCreate,
    db: Session = Depends(get_db)
):
    # Shared with the Telegram inbound callback path (I1b) so both writes are
    # identical: upsert + re-analyze + conditional A4 fuller-turn trigger.
    return write_checkin(db, activity_id, checkin_data)
