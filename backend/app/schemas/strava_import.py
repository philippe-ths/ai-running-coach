from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StravaImportCreate(BaseModel):
    """Request to start a historical Strava import from `since_date` to today."""

    since_date: date


class StravaImportRead(BaseModel):
    """Progress view of a historical import for the status poll."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    since_date: date
    status: str
    activities_imported: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime
