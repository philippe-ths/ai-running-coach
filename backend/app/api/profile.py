from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.db.session import get_db
from app.models import User, UserProfile
from app.schemas import UserProfileRead, UserProfileCreate

router = APIRouter()

def get_current_user_profile(db: Session, auto_create_user: bool = True) -> UserProfile:
    """
    Helper for Local MVP: gets the first available user's profile,
    creating defaults if necessary.
    """
    user = db.execute(select(User)).scalars().first()
    if not user and auto_create_user:
        # Create a default user if none exists (safe for local MVP)
        user = User(email="local@runner.com")
        db.add(user)
        db.commit()
    
    if not user:
         raise HTTPException(status_code=404, detail="No user found")

    profile = db.execute(select(UserProfile).where(UserProfile.user_id == user.id)).scalars().first()
    if not profile:
        # Create default profile
        profile = UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=20,
            upcoming_races=[],
            max_hr=190
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    
    return profile

@router.get("/profile", response_model=UserProfileRead)
def read_profile(db: Session = Depends(get_db)):
    """Get the current user's profile."""
    return get_current_user_profile(db)

@router.put("/profile", response_model=UserProfileRead)
def update_profile(profile_in: UserProfileCreate, db: Session = Depends(get_db)):
    """Update the current user's profile."""
    existing_profile = get_current_user_profile(db)
    
    # Update fields
    for field, value in profile_in.model_dump(exclude_unset=True).items():
        setattr(existing_profile, field, value)
        
    db.add(existing_profile)
    db.commit()
    db.refresh(existing_profile)
    return existing_profile
