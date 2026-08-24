import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Date, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)

    goal_type: Mapped[str] = mapped_column(String)  # 5k, marathon, general
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    experience_level: Mapped[str] = mapped_column(String)  # new, intermediate, advanced
    weekly_days_available: Mapped[int] = mapped_column(Integer)
    current_weekly_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "user_entered", "race_estimate", "lab_test", or "runner_confirmed" (#945:
    # the runner confirmed a coach-offered revision backed by their own
    # observed evidence -- see app/services/coach/max_hr_calibration.py).
    max_hr_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # #945: bookkeeping for the max-HR revision detector's anti-nag rule only --
    # never read as a fact about the runner. The value/timestamp of the last
    # `revise_max_hr` offer actually put in front of the runner, so the
    # detector does not re-raise the same evidence on every turn. Cleared once
    # the runner confirms a revision. Null means "never offered".
    max_hr_revision_last_surfaced_value: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    max_hr_revision_last_surfaced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Manual resting HR (bpm), the interim source ahead of a device integration
    # (#555). Null until the runner enters it; the referral layer abstains while
    # absent, and #166's sustained-rise red flag activates once a trend source lands.
    resting_hr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The runner's build (#742). Null means NOT STATED, which is not the same as
    # average: the coach pack drops the signal entirely rather than substituting a
    # typical runner. Raw facts only -- no BMI or other derived index is stored or
    # computed anywhere, because a ratio invites the population formula the North
    # Star's "coach this runner, not the median" exists to refuse. Float because a
    # runner who tracks 78.4 kg should not be rounded to an integer.
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The runner's own HR-zone lower bounds (5 ascending bpm values), pulled from
    # their Strava athlete zones so time-in-zone matches what Strava shows (#297).
    # Null until first synced; analysis then falls back to the %-of-max-HR scheme.
    hr_zones: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # List[int], len 5
    hr_zones_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "strava"
    upcoming_races: Mapped[list] = mapped_column(JSON, default=[])  # List[dict]
    injury_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Opt-in medication/physiology flag (N4 confounder stage reads it). Nullable:
    # unset means "unknown", not "no stimulant use".
    stimulant_use: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # The day the runner's week starts, in Python weekday() space (0=Monday,
    # 6=Sunday). Null resolves to Monday, so every pre-existing row and every
    # runner who has not chosen keeps byte-identical "this week" framing across
    # the coach pack and the Trends API (#676). The product offers only Monday or
    # Sunday; the single week-boundary definition in services/weeks.py is general.
    week_starts_on: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", back_populates="profile")
