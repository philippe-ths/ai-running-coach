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


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileRead(UserProfileBase):
    user_id: UUID
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
