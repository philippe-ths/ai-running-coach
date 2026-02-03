import uuid
from datetime import datetime, date
from typing import Optional, Any
from sqlalchemy import (
    String, Integer, Float, ForeignKey, DateTime, Boolean, Date, BigInteger, Text, JSON, Uuid
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# --- Utils ---
def generate_uuid():
    return uuid.uuid4()

# --- 5.1 Users ---
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strava_account = relationship("StravaAccount", back_populates="user", uselist=False)
    activities = relationship("Activity", back_populates="user")
    profile = relationship("UserProfile", back_populates="user", uselist=False)

# --- 5.2 Strava Accounts ---
class StravaAccount(Base):
    __tablename__ = "strava_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    strava_athlete_id: Mapped[int] = mapped_column(Integer, unique=True)
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str] = mapped_column(String)
    expires_at: Mapped[int] = mapped_column(Integer) # Unix timestamp
    scope: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User", back_populates="strava_account")

# --- 5.3 Activities ---
class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    strava_activity_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    
    distance_m: Mapped[int] = mapped_column(Integer) # e.g. 5000
    moving_time_s: Mapped[int] = mapped_column(Integer)
    elapsed_time_s: Mapped[int] = mapped_column(Integer)
    elev_gain_m: Mapped[float] = mapped_column(Float, default=0.0)
    
    avg_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_speed_mps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # New Field
    user_intent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    raw_summary: Mapped[dict] = mapped_column(JSON, default={})
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User", back_populates="activities")
    metrics = relationship("DerivedMetric", back_populates="activity", uselist=False, cascade="all, delete-orphan")
    advice = relationship("Advice", back_populates="activity", uselist=False, cascade="all, delete-orphan")
    check_in = relationship("CheckIn", back_populates="activity", uselist=False, cascade="all, delete-orphan")
    streams = relationship("ActivityStream", back_populates="activity", cascade="all, delete-orphan")

# --- 5.4 Activity Streams ---
class ActivityStream(Base):
    __tablename__ = "activity_streams"
    
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"))
    stream_type: Mapped[str] = mapped_column(String) # time, distance, watts, heartrate
    data: Mapped[list] = mapped_column(JSON)

    activity = relationship("Activity", back_populates="streams")

# --- 5.5 Derived Metrics ---
class DerivedMetric(Base):
    __tablename__ = "derived_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"), unique=True)
    
    activity_class: Mapped[str] = mapped_column(String) # Interval, Tempo, Long, etc.
    effort_score: Mapped[float] = mapped_column(Float)
    pace_variability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hr_drift: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    time_in_zones: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=[]) # list[str]
    confidence: Mapped[str] = mapped_column(String) # low, medium, high
    confidence_reasons: Mapped[list] = mapped_column(JSON, default=[])

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    activity = relationship("Activity", back_populates="metrics")

# --- 5.6 Advice ---
class Advice(Base):
    __tablename__ = "advice"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"), unique=True)
    
    verdict: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=[])   # list[str]
    next_run: Mapped[dict] = mapped_column(JSON, default={})   # structured
    week_adjustment: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=[])   # list[str]
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    full_text: Mapped[str] = mapped_column(Text)
    
    # New AI Field
    ai_commentary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # New Structured Field (JSONB in Postgres)
    structured_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    activity = relationship("Activity", back_populates="advice")

# --- 5.7 User Profile ---
class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    
    goal_type: Mapped[str] = mapped_column(String) # 5k, marathon, general
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    experience_level: Mapped[str] = mapped_column(String) # new, intermediate, advanced
    weekly_days_available: Mapped[int] = mapped_column(Integer)
    current_weekly_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upcoming_races: Mapped[list] = mapped_column(JSON, default=[]) # List[dict]
    injury_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User", back_populates="profile")

# --- 5.8 Check Ins ---
class CheckIn(Base):
    __tablename__ = "check_ins"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"))
    
    rpe: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pain_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pain_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sleep_quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    activity = relationship("Activity", back_populates="check_in")
