from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from typing import Optional
from uuid import UUID

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.db.session import get_db
from app.models import CoachingRelationship, User, StravaAccount, UserProfile
from app.schemas import UserProfileRead, UserProfileCreate

router = APIRouter()


def _lookup_relationship(db: Session, user_id: UUID) -> Optional[CoachingRelationship]:
    return db.execute(
        select(CoachingRelationship).where(CoachingRelationship.user_id == user_id)
    ).scalars().first()


def _lookup_profile(db: Session, user_id: UUID) -> Optional[UserProfile]:
    return db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalars().first()


def _get_or_create_singleton(db: Session, lookup, create):
    """Race-safe get-or-create for a per-user singleton row (#598).

    A new user's first authenticated screen fires /profile, /coach/voice and
    /coach/stance concurrently; each misses the SELECT below and attempts the
    INSERT, and the losers violate the PK/unique constraint. Recover the way
    ``resolve_user_by_email`` does for User: roll back the failed INSERT, expunge
    the doomed instance so a later autoflush cannot retry it, and reuse the row
    the winner committed.
    """
    existing = lookup()
    if existing is not None:
        return existing
    row = create()
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if row in db:
            db.expunge(row)
        existing = lookup()
        if existing is None:
            raise
        return existing
    db.refresh(row)
    return row


def get_current_user_profile(
    db: Session,
    user: Optional[User] = None,
    auto_create_user: bool = True,
) -> UserProfile:
    """Get the current user's profile, creating defaults if necessary.

    Phase 2 (ADR 0022): ``user`` is the identity resolved from the verified
    Clerk session, passed by the profile endpoints. When it is None we fall back
    to the legacy single-user resolution -- the Strava-linked user, else the
    first user -- which still serves local dev and the (Clerk-disabled) test
    suite. Auto-create of a blank ``local@runner.com`` user only happens on the
    legacy path with no user at all; under Clerk the user is created from the
    verified email by resolve_user_by_email, never auto-created blank here.
    """
    if user is None:
        strava_account = db.execute(select(StravaAccount)).scalars().first()
        user = strava_account.user if strava_account else db.execute(select(User)).scalars().first()
        if not user and auto_create_user and not settings.clerk_enabled:
            user = User(email="local@runner.com")
            db.add(user)
            db.commit()

    if not user:
        raise HTTPException(status_code=404, detail="No user found")

    # A1: the thin coaching_relationship singleton anchors the relationship from
    # first contact, auto-created the way the default profile is below. Both
    # get-or-creates are race-safe (#598): a fresh user's first screen fires
    # several requests at once and the losers must recover, not 500.
    _get_or_create_singleton(
        db,
        lambda: _lookup_relationship(db, user.id),
        lambda: CoachingRelationship(user_id=user.id),
    )

    profile = _get_or_create_singleton(
        db,
        lambda: _lookup_profile(db, user.id),
        lambda: UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=20,
            upcoming_races=[],
            max_hr=190,
        ),
    )

    return profile

@router.get("/profile", response_model=UserProfileRead)
def read_profile(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(verify_clerk_session),
):
    """Get the current user's profile."""
    return get_current_user_profile(db, user)

@router.put("/profile", response_model=UserProfileRead)
def update_profile(
    profile_in: UserProfileCreate,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(verify_clerk_session),
):
    """Update the current user's profile."""
    existing_profile = get_current_user_profile(db, user)
    
    # Update fields
    for field, value in profile_in.model_dump(exclude_unset=True).items():
        setattr(existing_profile, field, value)
        
    db.add(existing_profile)
    db.commit()
    db.refresh(existing_profile)
    return existing_profile
