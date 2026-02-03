import json
import os
from pathlib import Path
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.db.session import get_db
from app.models import User, UserProfile
from app.services import activity_service
from app.services.analysis import engine as analysis_engine
from app.services.coaching import engine as coaching_engine

router = APIRouter()

@router.post("/demo/load")
def load_demo_data(db: Session = Depends(get_db)):
    """
    Loads sample activities, runs analysis and coaching logic.
    Only available if DEMO_MODE=1.
    """
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    # 1. Ensure Demo User
    demo_email = "demo@example.com"
    stmt = select(User).where(User.email == demo_email)
    user = db.execute(stmt).scalars().first()
    
    if not user:
        user = User(email=demo_email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 2. Ensure Profile
    stmt = select(UserProfile).where(UserProfile.user_id == user.id)
    profile = db.execute(stmt).scalars().first()
    
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            goal_type="5k_improver",
            experience_level="intermediate",
            weekly_days_available=4
        )
        db.add(profile)
        db.commit()

    # 3. Load Samples
    # Locate sample_data relative to this file: backend/app/api/demo.py -> .../backend/sample_data
    # backend/app/api/demo.py -> backend/app/api -> backend/app -> backend -> backend/sample_data
    base_path = Path(__file__).resolve().parents[2]
    samples_dir = base_path / "sample_data" / "strava" / "activities"
    
    if not samples_dir.exists():
        raise HTTPException(status_code=500, detail=f"Sample data not found at {samples_dir}")

    results = []
    
    # Sort for consistent order (by filename)
    json_files = sorted(samples_dir.glob("*.json"))
    
    for json_file in json_files:
        with open(json_file, "r") as f:
            raw_data = json.load(f)
            
        # A. Upsert Activity
        # We manually inject the athlete ID in the raw data to match user logic if needed,
        # but upsert_activity relies on arguments for user_id.
        activity = activity_service.upsert_activity(db, raw_data, user.id)
        db.commit()
        
        # B. Run Analysis
        metrics = analysis_engine.run_analysis(db, activity.id)
        
        # C. Run Coaching
        advice = coaching_engine.generate_and_save_advice(db, activity.id)
        
        results.append({
            "strava_id": activity.strava_activity_id,
            "name": activity.name,
            "analysis": "ok" if metrics else "failed",
            "advice": "ok" if advice else "failed"
        })

    return {
        "status": "success",
        "user_email": demo_email,
        "processed_count": len(results),
        "details": results
    }
