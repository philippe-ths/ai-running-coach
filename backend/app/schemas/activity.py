from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.services.units.cadence import normalize_cadence_spm


class ActivityBase(BaseModel):
    strava_activity_id: int
    name: str
    type: str
    start_date: datetime
    distance_m: int
    moving_time_s: int
    elapsed_time_s: int
    elev_gain_m: float
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    raw_summary: Dict[str, Any] = {}


class ActivityCreate(ActivityBase):
    pass


class ActivityRead(ActivityBase):
    id: UUID
    user_id: UUID
    is_deleted: bool
    user_intent: Optional[str] = None
    avg_cadence: Optional[float] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("avg_cadence", mode="before")
    @classmethod
    def read_cadence(cls, v: Optional[float]) -> Optional[float]:
        return v

    @field_validator("avg_cadence", mode="after")
    @classmethod
    def normalize_cadence(cls, v: Optional[float], info) -> Optional[float]:
        return v

    @model_validator(mode="after")
    def normalize_run_cadence(self) -> "ActivityRead":
        effective_type = self.user_intent if self.user_intent else self.type
        self.avg_cadence = normalize_cadence_spm(effective_type, self.avg_cadence)
        return self


class ActivityIntentUpdate(BaseModel):
    user_intent: str
