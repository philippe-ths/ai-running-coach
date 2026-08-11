"""Schedule request/response schemas (#830).

Three vocabularies the rest of the feature is built from — `SessionIntent`,
`Discipline`, `Commitment` — declared once here as `Literal`s so the API, the
store and (later) the coach's structured output all coerce against the same set.
`ScreenPointer` is the precedent: a closed `Literal` plus `extra="forbid"` means
an off-contract value fails at the boundary rather than reaching a column.

`SpacingRule` follows the `ProposedActionRequest` idiom deliberately: one flat
model whose per-kind arguments are optional fields, with a `model_validator`
enforcing the required set for each kind. A tagged union would read more neatly
in isolation, but this shape is what the house already validates LLM-supplied
arguments with, and this rule set will be LLM-supplied.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.coach_context import VolumeMetricComparison

# --- the three axes --------------------------------------------------------

SessionIntent = Literal["rest", "easy", "long", "quality", "strength"]
Discipline = Literal["run", "walk", "bike", "strength", "row", "other"]
Commitment = Literal["committed", "suggested"]

# Derived, never stored: see models/planned_session.py.
Placement = Literal["pinned", "window", "week"]
SessionStatus = Literal["upcoming", "done", "missed", "dismissed"]

RuleKind = Literal[
    "rest_day_after",
    "no_intent_day_before",
    "min_days_between",
    "preferred_days",
    "max_sessions_per_day",
]


# --- rules -----------------------------------------------------------------


class SpacingRule(BaseModel):
    """One spacing constraint the week must satisfy.

    The kinds are a closed vocabulary with a pure predicate each
    (`services/schedule/rules.py`), which is what makes a violation DETECTABLE
    rather than merely readable. The four rules the design's flexible week is
    made of all express here:

        "No quality run the day before the long run"
            -> no_intent_day_before(before_intent=quality, target_intent=long)
        "No heavy legs the day before a quality run"
            -> no_intent_day_before(before_intent=strength, target_intent=quality)
        "A full rest day after the long run"
            -> rest_day_after(intent=long)
        "Long run needs a free morning — Sat or Sun"
            -> preferred_days(intent=long, weekdays=[5, 6])

    `label` is the runner-facing sentence. It is carried rather than generated so
    the coach can phrase a rule in the runner's own terms; the PREDICATE is what
    the checker runs, so a mismatched label can mislead a reader but can never
    change what is enforced.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RuleKind
    label: str = Field(min_length=1, max_length=200)
    source: Literal["coach", "runner"] = "coach"

    # rest_day_after / preferred_days
    intent: Optional[SessionIntent] = None
    # no_intent_day_before
    before_intent: Optional[SessionIntent] = None
    target_intent: Optional[SessionIntent] = None
    # min_days_between
    intent_a: Optional[SessionIntent] = None
    intent_b: Optional[SessionIntent] = None
    days: Optional[int] = Field(default=None, ge=1, le=7)
    # preferred_days — 0 = Monday .. 6 = Sunday, matching services/weeks.py
    weekdays: Optional[List[int]] = Field(default=None, min_length=1, max_length=7)
    # max_sessions_per_day
    count: Optional[int] = Field(default=None, ge=1, le=5)

    @field_validator("weekdays")
    @classmethod
    def _weekdays_in_range(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None and any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays must be 0 (Monday) through 6 (Sunday)")
        return v

    @model_validator(mode="after")
    def _validate_shape(self) -> "SpacingRule":
        if self.kind == "rest_day_after" and self.intent is None:
            raise ValueError("rest_day_after requires intent")
        if self.kind == "no_intent_day_before" and (
            self.before_intent is None or self.target_intent is None
        ):
            raise ValueError(
                "no_intent_day_before requires before_intent and target_intent"
            )
        if self.kind == "min_days_between" and (
            self.intent_a is None or self.intent_b is None or self.days is None
        ):
            raise ValueError("min_days_between requires intent_a, intent_b and days")
        if self.kind == "preferred_days" and (
            self.intent is None or not self.weekdays
        ):
            raise ValueError("preferred_days requires intent and weekdays")
        if self.kind == "max_sessions_per_day" and self.count is None:
            raise ValueError("max_sessions_per_day requires count")
        return self


class RuleViolation(BaseModel):
    """A rule the current placement cannot satisfy."""

    model_config = ConfigDict(extra="forbid")

    kind: RuleKind
    label: str
    detail: str


# --- the horizon's week shape ----------------------------------------------


class PlannedWeekShape(BaseModel):
    """One week of the horizon, as SHAPE rather than sessions.

    Concrete for ~3 weeks, shape only beyond (a settled design decision). A week
    that also has `planned_sessions` rows reads as planned; a week with only this
    reads as sketched. Nothing stores which — see `services/schedule/horizon.py`.
    """

    model_config = ConfigDict(extra="forbid")

    week_start: date
    phase: Optional[str] = Field(default=None, max_length=60)
    target_running_distance_m: Optional[float] = Field(default=None, ge=0)
    target_effort_score: Optional[float] = Field(default=None, ge=0)
    # discipline -> share of the week's load, 0..1. Shares, not absolutes, so a
    # mix cannot contradict the total it is a mix of.
    discipline_mix: Dict[str, float] = Field(default_factory=dict)
    intent_mix: Dict[str, float] = Field(default_factory=dict)


# --- sessions --------------------------------------------------------------


class SessionStructure(BaseModel):
    """The interval shape `workout_matching.match_planned_to_detected` expects.

    Its exact keys, unchanged: that function has compared a plan to detected reps
    since the beginning and has never once been handed one, because
    `_extract_planned_workout` is a placeholder that returns None.
    """

    model_config = ConfigDict(extra="forbid")

    reps_planned: int = Field(ge=1, le=60)
    rep_distance_m: Optional[float] = Field(default=None, gt=0)
    rest_s: Optional[float] = Field(default=None, ge=0)


class PlannedSessionRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    window_start: date
    window_end: date
    # Derived from the window; see models/planned_session.py.
    placement: Placement
    # `max(window_start, today) .. window_end`, or None once the window has
    # passed. The stored window never moves.
    effective_window_start: Optional[date] = None
    effective_window_end: Optional[date] = None
    # True once time has eaten into the window — what the design shows as
    # "window narrowed from Thu-Sat". Carried rather than left for the client to
    # re-derive, so the stored-vs-effective comparison is made in one place.
    has_narrowed: bool = False

    intent: SessionIntent
    discipline: Discipline
    commitment: Commitment
    status: SessionStatus

    title: str
    detail: Optional[str] = None
    target_distance_m: Optional[float] = None
    target_duration_s: Optional[int] = None
    target_effort_score: Optional[float] = None
    structure: Optional[Dict[str, Any]] = None

    completed_at: Optional[datetime] = None
    completed_activity_id: Optional[UUID] = None
    completion_source: Optional[str] = None
    dismissed_at: Optional[datetime] = None


# --- logged actuals --------------------------------------------------------


class LoggedActivityRead(BaseModel):
    """What the runner actually did this week, from the one fact stream.

    Projected from `activity_facts`, never re-queried: the schedule must not be a
    second opinion about a week's totals.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: Optional[UUID] = None
    local_date: date
    activity_type: str
    discipline: Discipline
    distance_m: float
    moving_time_s: int
    effort_score: float


# --- the week read ---------------------------------------------------------


class DisciplineLoad(BaseModel):
    """One discipline's slice of the week, planned and logged.

    Sized by `effort_score` because it is the only unit that sums honestly across
    a gym session and a turbo ride; km cannot, since strength and an indoor bike
    have no distance.
    """

    model_config = ConfigDict(extra="forbid")

    discipline: Discipline
    planned_effort_score: float = 0.0
    logged_effort_score: float = 0.0
    planned_sessions: int = 0
    logged_sessions: int = 0


class WeekHeadline(BaseModel):
    """Running km leads; load carries the disciplines km cannot measure.

    A settled decision: km is how a runner thinks about running and running is
    the priority sport, so the headline is run distance — with the discipline mix
    beneath it sized by load so strength, bike and row stay visible as progress.
    """

    model_config = ConfigDict(extra="forbid")

    planned_running_distance_m: Optional[float] = None
    logged_running_distance_m: float = 0.0
    planned_sessions: int = 0
    done_sessions: int = 0


class ScheduleWeekRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    week_end: date
    is_current_week: bool
    # Whether an ACTIVE plan row exists. Note for the UI: free mode is "no
    # COMMITTED sessions" (`headline.planned_sessions == 0`), which is the state
    # the design describes — a plan carrying only suggestions is still free.
    # Render the free week off the headline, not off this flag.
    has_plan: bool
    plan_id: Optional[UUID] = None

    headline: WeekHeadline
    sessions: List[PlannedSessionRead] = Field(default_factory=list)
    logged: List[LoggedActivityRead] = Field(default_factory=list)
    by_discipline: List[DisciplineLoad] = Field(default_factory=list)
    rules: List[SpacingRule] = Field(default_factory=list)
    violations: List[RuleViolation] = Field(default_factory=list)

    # The runner's own typical week, straight from the existing volume builder —
    # `norm_weekly` is ALL-ACTIVITY by that definition, with `current_runs`
    # carried alongside, so "typical" means exactly what it means on Trends.
    # Present only for the current week, where "as of today" is meaningful.
    norm: Optional[List[VolumeMetricComparison]] = None


# --- the horizon read ------------------------------------------------------


class HorizonWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    phase: Optional[str] = None
    # True when the week has real sessions; False when it is shape only. Derived
    # from what is actually there, so it cannot claim more than the plan holds.
    planned: bool
    is_current: bool
    running_distance_m: Optional[float] = None
    effort_score: Optional[float] = None
    discipline_mix: Dict[str, float] = Field(default_factory=dict)
    intent_mix: Dict[str, float] = Field(default_factory=dict)


class GoalRaceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    race_date: date
    distance_m: float
    priority: str


class ScheduleHorizonRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weeks: List[HorizonWeek] = Field(default_factory=list)
    races: List[GoalRaceRead] = Field(default_factory=list)
    has_plan: bool
    # The largest weekly load in the window; bar lengths are true proportions of
    # it, so the ramp reads honestly instead of every bar looking maxed out.
    peak_effort_score: Optional[float] = None


# --- goal race writes ------------------------------------------------------


class GoalRaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    race_date: date
    distance_m: float = Field(gt=0, le=1_000_000)
    priority: Literal["A", "B", "C"] = "A"
