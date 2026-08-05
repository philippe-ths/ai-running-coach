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
    # The runner's local wall-clock start (#399), serialised naive (no offset) so
    # the frontend renders the activity's own local time regardless of the
    # viewer's timezone. Null for any pre-#399 row not yet backfilled.
    start_date_local: Optional[datetime] = None
    distance_m: int
    moving_time_s: int
    elapsed_time_s: int
    elev_gain_m: float
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None


class ActivityCreate(ActivityBase):
    # The full Strava summary JSON, persisted on create. Deliberately NOT on the
    # list read model (ActivityRead): that payload ships per item on the home page
    # and never reads raw_summary (#359). The detail read model re-adds it.
    raw_summary: Dict[str, Any] = {}


class ActivityRead(ActivityBase):
    id: UUID
    user_id: UUID
    is_deleted: bool
    user_intent: Optional[str] = None
    avg_cadence: Optional[float] = None
    # Classification headline composed at read time from DerivedMetric axes;
    # null until the activity has been analysed (#136).
    headline: Optional[str] = None
    # The coach report's opening claim, projected at read time (#797). Null when
    # the run has no displayable non-fallback report yet. Telegram is the only
    # channel that announces a report, so this is how a runner who has not linked
    # one discovers their runs were coached at all.
    coach_lead: Optional[str] = None
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
    # Optional: null clears the stated intent (the runner is leaving the
    # activity to the measured classification rather than overriding it).
    user_intent: Optional[str] = None
