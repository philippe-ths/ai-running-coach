from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckInBase(BaseModel):
    rpe: Optional[int] = None
    pain_score: Optional[int] = None
    pain_location: Optional[str] = None
    sleep_quality: Optional[int] = None
    notes: Optional[str] = None


class CheckInCreate(CheckInBase):
    # Server-side range validation at the write boundary (#156). Domain ranges
    # per CONTEXT.md: RPE 1-10 (Borg), pain_score 0-10. Out-of-range input is
    # rejected (422) rather than silently stored and clamped downstream. The
    # read model stays permissive so any legacy out-of-range row still serialises.
    rpe: Optional[int] = Field(default=None, ge=1, le=10)
    pain_score: Optional[int] = Field(default=None, ge=0, le=10)


class CheckInRead(CheckInBase):
    id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
