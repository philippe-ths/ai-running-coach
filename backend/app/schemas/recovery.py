"""Pydantic schemas for /api/recovery (#555)."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class RecoveryDayRead(BaseModel):
    """One night. A null signal means NOT MEASURED, never zero."""

    model_config = ConfigDict(from_attributes=True)

    day: date
    source: str
    sleep_duration_s: Optional[int] = None
    sleep_score: Optional[int] = None
    hrv_avg_ms: Optional[float] = None
    hrv_status: Optional[str] = None
    hrv_baseline_low_ms: Optional[float] = None
    hrv_baseline_high_ms: Optional[float] = None
    resting_hr: Optional[int] = None
    fetched_at: datetime


class RecoveryResponse(BaseModel):
    days: List[RecoveryDayRead]
