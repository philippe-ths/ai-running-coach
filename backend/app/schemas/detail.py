from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.activity import ActivityRead
from app.schemas.checkin import CheckInRead
from app.services.units.cadence import normalize_cadence_spm


class DerivedMetricRead(BaseModel):
    activity_class: str
    effort_score: float
    pace_variability: Optional[float] = None
    hr_drift: Optional[float] = None
    flags: List[str] = []
    confidence: str
    confidence_reasons: List[str] = []
    time_in_zones: Optional[Dict] = None
    model_config = ConfigDict(from_attributes=True)


class ActivityStreamRead(BaseModel):
    stream_type: str
    data: List[Any]
    model_config = ConfigDict(from_attributes=True)


class ActivityDetailRead(ActivityRead):
    metrics: Optional[DerivedMetricRead] = None
    check_in: Optional[CheckInRead] = None
    streams: List[ActivityStreamRead] = []

    @model_validator(mode="after")
    def normalize_stream_cadence(self) -> "ActivityDetailRead":
        effective_type = self.user_intent if self.user_intent else self.type

        if not self.streams:
            return self

        cadence_stream = next(
            (s for s in self.streams if s.stream_type == "cadence"), None
        )
        if not cadence_stream:
            return self

        test_val = 80.0
        norm_val = normalize_cadence_spm(effective_type, test_val)
        should_normalize = norm_val == 160.0

        if should_normalize:
            nums = [x for x in cadence_stream.data if isinstance(x, (int, float))]
            if not nums:
                return self

            stream_avg = sum(nums) / len(nums)

            if stream_avg < 130:
                cadence_stream.data = [
                    x * 2 if isinstance(x, (int, float)) else x
                    for x in cadence_stream.data
                ]

        return self
