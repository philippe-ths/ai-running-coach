from datetime import date, datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UserProfileBase(BaseModel):
    goal_type: str
    target_date: Optional[date] = None
    experience_level: str
    weekly_days_available: int
    current_weekly_km: Optional[int] = None
    max_hr: Optional[int] = None
    max_hr_source: Optional[str] = None  # "user_entered", "race_estimate", "lab_test"
    upcoming_races: List[Dict[str, Any]] = []
    injury_notes: Optional[str] = None


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileRead(UserProfileBase):
    user_id: UUID
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
