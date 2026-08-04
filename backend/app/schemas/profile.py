from datetime import date, datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.weeks import MONDAY, SUNDAY


class UserProfileBase(BaseModel):
    goal_type: str
    target_date: Optional[date] = None
    experience_level: str
    weekly_days_available: int
    current_weekly_km: Optional[int] = None
    max_hr: Optional[int] = None
    max_hr_source: Optional[str] = None  # "user_entered", "race_estimate", "lab_test"
    resting_hr: Optional[int] = None  # manual resting HR (bpm), interim source for #555
    # The runner's build (#742). Null = not stated, which the coach pack drops
    # rather than filling in with a typical runner.
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    upcoming_races: List[Dict[str, Any]] = []
    injury_notes: Optional[str] = None
    stimulant_use: Optional[bool] = None
    # Week start: Monday (0) or Sunday (6); null resolves to Monday (#676).
    week_starts_on: Optional[int] = None

    @field_validator("week_starts_on")
    @classmethod
    def _week_start_is_monday_or_sunday(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (MONDAY, SUNDAY):
            raise ValueError("week_starts_on must be 0 (Monday) or 6 (Sunday)")
        return v

    @field_validator("weight_kg")
    @classmethod
    def _weight_is_physiologically_possible(cls, v: Optional[float]) -> Optional[float]:
        # A generous envelope, not a judgement about what a runner should weigh.
        # It exists to catch a unit slip (pounds typed into a kg field reads as
        # ~240) and a stray keystroke, both of which would otherwise reach the
        # coach as a fact and skew its read of the runner's build.
        if v is not None and not (20 <= v <= 300):
            raise ValueError("weight_kg must be between 20 and 300 (kilograms)")
        return v

    @field_validator("height_cm")
    @classmethod
    def _height_is_physiologically_possible(cls, v: Optional[float]) -> Optional[float]:
        # Same intent: catches metres (1.87) and inches (74) typed into a cm field.
        if v is not None and not (100 <= v <= 250):
            raise ValueError("height_cm must be between 100 and 250 (centimetres)")
        return v


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileRead(UserProfileBase):
    user_id: UUID
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
