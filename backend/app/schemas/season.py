"""The season: the coach's read of every goal, and the timeline it plans to (#1064).

A goal is whatever the runner wrote. The season is the coach's OPINION of it:
what kind of goal it is, what achieving it means, when to do it, which real
events would serve it, and how the months between now and then are spent. Code
never invents any of that; it checks it (`services/schedule/season_check.py`)
and does the arithmetic the opinion implies (`services/schedule/frames.py`).

One field is a rule rather than an opinion: a challenge's `ChallengeRule`. Once
the season is active, every week the rule covers must meet it, and that is
checked in code against the runner's own heart-rate history, never estimated by
the model.

Stored as JSON on `seasons.plan` and strict-coerced on read, the same idiom as
`TrainingPlan.week_shapes`: an off-shape value fails at the boundary rather than
reaching a screen or a prompt.
"""

from datetime import date, datetime, timedelta
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.schedule import Discipline, GoalRaceRead

GoalKind = Literal["challenge", "race", "finish", "completion", "someday"]
PhaseKind = Literal["base", "build", "sharpen", "taper", "race", "recover"]
ChallengeMetric = Literal["zone_time_s", "time_s", "distance_m", "sessions"]
SeasonStatus = Literal["drafting", "active", "superseded", "failed"]

# `SuggestedEvent` and `GoalView` have a field NAMED `date`. Inside the class
# body that name is the field's default (None) from its own line on, so a later
# annotation written `Optional[date]` resolves to `Optional[None]` and refuses
# every real date. Annotations in those two classes use this alias instead.
Day = date


def _single_line(value: Optional[str]) -> Optional[str]:
    # Event names and links can come from web pages: a line break in one could
    # forge a second line wherever the season is rendered into a prompt.
    if value is None:
        return value
    if any(ch in value for ch in "\r\n\t\x00") or any(ord(ch) < 32 for ch in value):
        raise ValueError("must be a single line of plain text")
    return value.strip()


class ChallengeRule(BaseModel):
    """At least `at_least` of `metric` in every week for `weeks` straight weeks.

    `at_least` is in the metric's own unit: seconds for the two time metrics,
    metres for distance, a count for sessions. `disciplines` empty means every
    activity counts. `start` is the first covered week's start, on the runner's
    week boundary.
    """

    model_config = ConfigDict(extra="forbid")

    metric: ChallengeMetric
    disciplines: List[Discipline] = Field(default_factory=list)
    min_zone: Optional[int] = Field(default=None, ge=1, le=5)
    at_least: float = Field(gt=0)
    weeks: int = Field(ge=1, le=52)
    start: date

    @model_validator(mode="after")
    def _zone_only_for_zone_time(self) -> "ChallengeRule":
        if (self.metric == "zone_time_s") != (self.min_zone is not None):
            raise ValueError("min_zone is required for zone_time_s and only for it")
        return self

    @property
    def last_week_start(self) -> date:
        return self.start + timedelta(weeks=self.weeks - 1)

    def covers(self, week_start: date) -> bool:
        return self.start <= week_start <= self.last_week_start


class SuggestedEvent(BaseModel):
    """A real event the coach found and recommends entering. Never booked: only
    the runner can book, by editing the goal."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    date: Optional[Day] = None
    window_start: Optional[Day] = None
    window_end: Optional[Day] = None
    distance_m: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    url: str = Field(max_length=500)
    why: str = Field(default="", max_length=300)

    @field_validator("name", "url", "why")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return _single_line(value)

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("url must be http(s)")
        return value


class GoalView(BaseModel):
    """The coach's read of one of the runner's goals."""

    model_config = ConfigDict(extra="forbid")

    goal_id: UUID
    kind: GoalKind
    success: str = Field(min_length=1, max_length=300)
    # The day it happens (a race, a completion attempt), or a window when a day
    # is premature. A booked goal's day is the runner's and must not move.
    date: Optional[Day] = None
    window_start: Optional[Day] = None
    window_end: Optional[Day] = None
    approach: str = Field(min_length=1, max_length=800)
    events: List[SuggestedEvent] = Field(default_factory=list, max_length=3)
    challenge: Optional[ChallengeRule] = None

    @model_validator(mode="after")
    def _shape(self) -> "GoalView":
        if (self.kind == "challenge") != (self.challenge is not None):
            raise ValueError("a challenge goal carries a rule, and only a challenge")
        if self.date is not None and (self.window_start or self.window_end):
            raise ValueError("a date or a window, not both")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("a window needs both ends")
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("window ends before it starts")
        if self.kind == "someday" and (self.date or self.window_start):
            raise ValueError("a someday goal is not dated")
        return self


class SeasonPhase(BaseModel):
    """One stretch of the timeline, with the coach's targets for it.

    The targets are where the phase ENDS: frames interpolate each week towards
    them from where the previous phase ended, under the ramp cap.
    """

    model_config = ConfigDict(extra="forbid")

    kind: PhaseKind
    start: date
    end: date
    goal_id: Optional[UUID] = None
    focus: str = Field(default="", max_length=200)
    weekly_hours: Optional[float] = Field(default=None, ge=0, le=40)
    run_km: Optional[float] = Field(default=None, ge=0, le=300)
    long_run_km: Optional[float] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> "SeasonPhase":
        if self.end < self.start:
            raise ValueError("phase ends before it starts")
        return self


class SeasonPlan(BaseModel):
    """The whole season as the coach wrote it, after strict coercion."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1500)
    goals: List[GoalView] = Field(default_factory=list, max_length=20)
    phases: List[SeasonPhase] = Field(default_factory=list, max_length=40)

    def challenges(self) -> List[tuple]:
        """(goal_id, rule) for every challenge goal."""
        return [(g.goal_id, g.challenge) for g in self.goals if g.challenge is not None]

    def phase_on(self, day: date) -> Optional[SeasonPhase]:
        for phase in self.phases:
            if phase.start <= day <= phase.end:
                return phase
        return None


# --- run log ---------------------------------------------------------------


class DraftAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: List[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class DraftLog(BaseModel):
    """What one model step's run cost and how its output fared against the
    checks: the numbers a model or prompt change is compared on."""

    model_config = ConfigDict(extra="forbid")

    model: str = ""
    attempts: List[DraftAttempt] = Field(default_factory=list)
    repairs: List[str] = Field(default_factory=list)
    # Weeks (or, for a season, checks) that passed on the first attempt.
    first_try_passed: int = 0
    checked: int = 0
    # What is still short after retry and repair, stated to the runner.
    shortfalls: List[str] = Field(default_factory=list)


# --- API -------------------------------------------------------------------


class ChallengeWeek(BaseModel):
    """One covered week of a challenge: what it needs, what the plan holds, and
    what the runner actually did once the week is past."""

    week_start: date
    index: int  # 1-based, "week 4 of 10"
    threshold: float
    planned: Optional[float] = None
    actual: Optional[float] = None
    met: Optional[bool] = None  # None while the week is not over


class ChallengeStatus(BaseModel):
    goal_id: UUID
    name: str
    rule: ChallengeRule
    weeks: List[ChallengeWeek]
    streak: int  # consecutive completed weeks met, counting from the start


class SeasonRead(BaseModel):
    """GET /api/schedule/season."""

    id: Optional[UUID] = None
    status: Optional[SeasonStatus] = None
    generated_at: Optional[datetime] = None
    model_id: Optional[str] = None
    plan: Optional[SeasonPlan] = None
    goals: List[GoalRaceRead] = Field(default_factory=list)
    challenges: List[ChallengeStatus] = Field(default_factory=list)
    # True when the runner's goals changed since this season was written.
    stale: bool = False
    # True while a newer season is being written behind an active one, so the
    # screen can keep showing the current season and say a rewrite is under way.
    regenerating: bool = False
    message: Optional[str] = None
