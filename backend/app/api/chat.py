from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID

from app.db.session import get_db
from app.schemas import ChatRequest, ChatResponse
from app.models import Activity, UserProfile
from app.services.ai.client import ai_client
from app.services.ai.prompts import build_chat_prompt

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
def chat_with_coach(payload: ChatRequest, db: Session = Depends(get_db)):
    """
    Context-aware chat endpoint.
    Retrieves activity context + profile + history to build the prompt.
    """
    # 1. Fetch Context Data
    activity = None
    metrics = None
    advice = None
    profile = None
    recent_history = []
    
    if payload.activity_id:
        activity = db.query(Activity).filter(Activity.id == payload.activity_id).first()
        if activity:
            metrics = activity.metrics
            advice = activity.advice
            
            # Fetch Profile associated with this activity's user
            profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
            
            # Fetch recent history (last 14 days relative to this activity)
            # Simplified: just last 5 activities for MVP context window
            recent_history = db.query(Activity).filter(
                Activity.user_id == activity.user_id,
                Activity.start_date < activity.start_date
            ).order_by(Activity.start_date.desc()).limit(5).all()

    # 2. Build Prompt
    full_prompt = build_chat_prompt(
        user_message=payload.message,
        activity=activity,
        metrics=metrics,
        advice=advice,
        profile=profile,
        recent_history=recent_history
    )
    
    # 3. Get Response
    reply = ai_client.generate_chat_response(full_prompt)
    
    return ChatResponse(reply=reply)
