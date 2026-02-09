from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CheckInBase(BaseModel):
    rpe: Optional[int] = None
    pain_score: Optional[int] = None
    pain_location: Optional[str] = None
    sleep_quality: Optional[int] = None
    notes: Optional[str] = None


class CheckInCreate(CheckInBase):
    pass


class CheckInRead(CheckInBase):
    id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
