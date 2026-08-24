"""Period report request/response schemas (#946).

A period report answers "how is this stretch of training going", runner-chosen
in both period and disciplines — never a longer write-up of one activity. The
async lifecycle mirrors `DraftStatusRead` (`services/schedule`): the write
endpoint returns immediately, the client polls the read endpoint until the row
leaves `generating`.
"""

from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PeriodReportStatus = Literal["generating", "ready", "failed"]


class PeriodReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    # Strava activity type strings (the `ActivityTypeFilter` vocabulary, e.g.
    # "Run"/"Ride"); empty means every discipline. Bounded (count and per-item
    # length) the same way every other runner-supplied string that reaches the
    # coach prompt is bounded elsewhere (`GoalRaceCreate.name`) — not because
    # this string is trusted less than those, but so an oversized payload cannot
    # inflate the stored identity key or the prompt for free.
    disciplines: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("disciplines")
    @classmethod
    def _strip_empties(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip() for v in value if v and v.strip()]
        if any(len(v) > 64 for v in cleaned):
            raise ValueError("each discipline must be at most 64 characters")
        return cleaned

    @model_validator(mode="after")
    def _period_is_sane(self) -> "PeriodReportCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        # A generous but real ceiling: this is a review of a stretch, not the
        # runner's whole history, and it bounds the pack a stronger model reads.
        if (self.period_end - self.period_start).days > 366:
            raise ValueError("a period report covers at most 366 days")
        return self


class PeriodReportContent(BaseModel):
    """The generated shape: prose first, a thin structured tail after — the A3
    prose-message discipline applied to a period rather than one activity.
    `extra="forbid"` so an off-contract model answer fails coercion rather than
    silently carrying an unreviewed field into storage."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    headline: Optional[str] = None
    next_steps: List[str] = Field(default_factory=list)


class PeriodReportRead(BaseModel):
    """The status poll + the finished report. `report` is null until `ready`."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    period_start: date
    period_end: date
    disciplines: List[str]
    status: PeriodReportStatus
    generated_at: Optional[datetime] = None
    created_at: datetime
    report: Optional[PeriodReportContent] = None
    # Runner-facing, mirroring the schedule draft's `DraftStatusRead.message`:
    # what is happening or what went wrong, in the runner's own terms, never the
    # gate's internal failure text.
    message: Optional[str] = None


class PeriodReportListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    period_start: date
    period_end: date
    disciplines: List[str]
    status: PeriodReportStatus
    generated_at: Optional[datetime] = None
    created_at: datetime
    # A short lead-in for the list row — the report's own headline, or the first
    # line of its message, so the list reads like a list of reports rather than
    # a list of dates.
    headline: Optional[str] = None
