from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.models import Activity, StravaAccount, CheckIn
from app.schemas import ActivityRead, ActivityDetailRead, CheckInCreate, CheckInRead, SyncResponse, ActivityIntentUpdate, DerivedMetricRead
from app.services import activity_service
from app.services.processing import engine as processing_engine
from app.services.processing.splits import calculate_splits

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
    metrics = await processing_engine.process_deep(db, str(activity_id))
    if not metrics:
        raise HTTPException(status_code=400, detail="Processing failed or activity not found.")
    
    return metrics

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
    processing_engine.process_activity(db, str(activity_id))
    
    return activity

@router.post("/sync", response_model=SyncResponse)
async def sync_activities(
    # In a real app, we'd get current_user from token.
    # Here, we optionally take an ID or default to the first account found.
    strava_athlete_id: Optional[int] = None, 
    db: Session = Depends(get_db)
):
    """
    Triggers a manual sync of the last 30 days of activities.
    """
    if strava_athlete_id:
        stmt = select(StravaAccount).where(StravaAccount.strava_athlete_id == strava_athlete_id)
        account = db.execute(stmt).scalars().first()
    else:
        # Default: take the first account (Single Player Mode)
        account = db.query(StravaAccount).first()
        
    if not account:
        raise HTTPException(status_code=404, detail="No linked Strava account found. Connect Strava first.")

    result = await activity_service.sync_recent_activities(db, account)
    return result

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
    return activity_service.get_activities(db, skip=skip, limit=limit)

@router.get("/activities/{activity_id}", response_model=ActivityDetailRead)
def read_activity(
    activity_id: UUID, 
    db: Session = Depends(get_db)
):
    activity = activity_service.get_activity(db, str(activity_id))
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
        
    # Lazy Data Repair: If activity_class is "Easy Run" but it's clearly an Indoor Ride/Walk etc, repair it on read.
    # This fixes stale data from earlier classifier versions without requiring full re-sync.
    if activity.metrics and activity.metrics.activity_class == "Easy Run":
       from app.services.processing.classifier import classify_activity
       
       # Pass an empty history for now (fast check)
       current_class = classify_activity(activity, []) 
       
       if current_class != "Easy Run":
           activity.metrics.activity_class = current_class
           db.add(activity.metrics)
           db.commit()
           db.refresh(activity)

    # Calculate Splits
    splits_data = []
    if activity.streams:
        effective_type = activity.user_intent if activity.user_intent else activity.type
        splits_data = calculate_splits(activity.streams, activity_type=effective_type)
        
    # Convert to Pydantic model manually to inject transient splits data
    response = ActivityDetailRead.model_validate(activity)
    response.splits = splits_data
    
    return response

@router.post("/activities/{activity_id}/checkin", response_model=CheckInRead)
def create_checkin(
    activity_id: UUID,
    checkin_data: CheckInCreate,
    db: Session = Depends(get_db)
):
    # 1. Upsert CheckIn
    existing = db.query(CheckIn).filter(CheckIn.activity_id == activity_id).first()
    if existing:
        for k, v in checkin_data.dict(exclude_unset=True).items():
            setattr(existing, k, v)
        db_obj = existing
    else:
        db_obj = CheckIn(activity_id=activity_id, **checkin_data.dict())
        db.add(db_obj)
    
    db.commit()
    db.refresh(db_obj)

    # 2. Trigger Re-Processing to incorporate user feedback
    processing_engine.process_activity(db, str(activity_id))

    return db_obj
