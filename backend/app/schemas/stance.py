"""P1.3 Stance — read/write schemas for the runner's declared coaching stance.

The runner edits a stance on their profile: a school choice (from the corpus) and
the two 1-5 emphasis dials (Data↔Sentiment, Process↔Outcome). The catalog (schools
+ axes + the balanced default) is served alongside so the UI renders the picker and
the radar from backend truth rather than duplicating it. Mirrors voice.py one layer
down. Stance carries NO free-text — runner-supplied coaching philosophy is
`User materials` (P4), an untrusted-input surface this milestone does not open.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.coach.corpus import DEFAULT_SCHOOL_ID, SCHOOLS
from app.services.coach.stance import DEFAULT_EMPHASIS, EMPHASIS_AXES, EMPHASIS_MAX, EMPHASIS_MIN


class StanceSelection(BaseModel):
    """The runner's stored stance selection (all optional; null = the default)."""

    school: Optional[str] = None
    data_sentiment: Optional[int] = Field(default=None, ge=EMPHASIS_MIN, le=EMPHASIS_MAX)
    process_outcome: Optional[int] = Field(default=None, ge=EMPHASIS_MIN, le=EMPHASIS_MAX)

    @field_validator("school")
    @classmethod
    def _known_school(cls, v):
        if v is not None and v not in SCHOOLS:
            raise ValueError(f"unknown coaching school: {v}")
        return v


class StanceSchoolInfo(BaseModel):
    key: str
    name: str
    stance: str  # the one-line summary the picker shows


class StanceAxisInfo(BaseModel):
    key: str
    low_pole: str
    high_pole: str


class StanceCatalog(BaseModel):
    """The static school cast + emphasis-axis labels + the default, for the picker."""

    schools: List[StanceSchoolInfo]
    axes: List[StanceAxisInfo]
    default_school: str
    default_data_sentiment: int
    default_process_outcome: int


class StanceConfigRead(BaseModel):
    """The runner's current stance plus the catalog the UI needs to render it."""

    current: StanceSelection
    catalog: StanceCatalog


def build_catalog() -> StanceCatalog:
    return StanceCatalog(
        schools=[
            StanceSchoolInfo(key=s.id, name=s.name, stance=s.stance)
            for s in SCHOOLS.values()
        ],
        axes=[
            StanceAxisInfo(key=a.key, low_pole=a.low_pole, high_pole=a.high_pole)
            for a in EMPHASIS_AXES
        ],
        default_school=DEFAULT_SCHOOL_ID,
        default_data_sentiment=DEFAULT_EMPHASIS.data_sentiment,
        default_process_outcome=DEFAULT_EMPHASIS.process_outcome,
    )
