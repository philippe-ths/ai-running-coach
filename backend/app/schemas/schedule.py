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

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.schemas.coach_context import VolumeMetricComparison
from app.services.schedule.planned_distance import (
    planned_distance_m as _planned_distance_m,
)

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


# #1082: how the coach wants a flexible week arranged, as a closed vocabulary.
# Unlike a SpacingRule these never forbid anything: they rank the legal
# arrangements the rules allow, so the week can be recommended rather than only
# checked. Each kind has one fixed reason the runner reads, written in code, so a
# recommendation can never claim more than its preference says.
PreferenceKind = Literal[
    "spread_hard_days",
    "easy_day_before_long",
    "strength_after_run",
    "spread_repeats",
]


class PlanPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PreferenceKind


class SessionAlternative(BaseModel):
    """Another way to fill the same slot (#1082): "easy run, or easy bike".

    The slot is still one activity. Its main session is the recommended option;
    an alternative is what the runner may do instead, sized in its own units.
    """

    model_config = ConfigDict(extra="forbid")

    intent: SessionIntent
    discipline: Discipline
    title: str
    detail: Optional[str] = None
    target_distance_m: Optional[float] = None
    target_duration_s: Optional[int] = None
    # How far it goes, by the one definition every reader asks
    # (`planned_distance.py`, #887), so the screen never works it out itself.
    planned_distance_m: float = 0.0


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

    `label` is the coach's OWN prose — carried so the coach can phrase a rule in
    its own terms — but it is not what the runner is told the rule IS (#844: a
    live label promised "or an easy walk" against a `rest_day_after` predicate
    that forbids exactly that, and the runner planned from the label rather than
    the predicate). The runner-facing STATEMENT of what a rule enforces is
    derived in code from `kind` + its arguments (`services/schedule/rule_text.py`
    `describe_rule`) on the READ side only — see `SpacingRuleRead` below — so it
    can never claim more or less than the PREDICATE (`rules.py`) actually checks.
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
    """A rule the current placement cannot satisfy.

    `statement` (#844) is the derived runner-facing text — see
    `SpacingRuleRead`/`rule_text.describe_rule` — and is what a violation should
    be READ as breaking. `label` is kept unchanged (the coach's own prose) so a
    caller that already renders it is not silently repointed at different text;
    it is additive, not a repurposing of what `label` means.
    """

    model_config = ConfigDict(extra="forbid")

    kind: RuleKind
    label: str
    statement: str
    detail: str


class SpacingRuleRead(SpacingRule):
    """`SpacingRule` plus its derived runner-facing `statement` (#844).

    Read-only and additive: `SpacingRuleRead` is never the type of a stored rule
    or of the LLM's drafted output (`draft_contract.DraftedPlan.rules` stays
    `List[SpacingRule]`), so nothing about what is persisted or what the coach is
    asked for changes. `statement` is computed at read time
    (`rule_text.describe_rule`) from `kind` + arguments — never from `label` — so
    it can never promise more than the rule's predicate (`rules.py`) enforces.
    """

    statement: str


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
    # How far the long run goes, and what the hard session is for (#980). A shape
    # is what a later pass writes real sessions from (#981), so it has to carry
    # the two things a coach and a runner actually settle about a distant week.
    # Both optional, and a shape stored before they existed simply reads None:
    # `week_shapes` is a JSON column, so an older row needs no migration and is
    # never rewritten.
    long_run_distance_m: Optional[float] = Field(default=None, ge=0)
    quality_focus: Optional[str] = Field(default=None, max_length=80)
    # The week's time across every activity and its walking distance (#1044),
    # as the coach stated them. Older shapes read None, as above.
    target_duration_s: Optional[float] = Field(default=None, ge=0)
    target_walking_distance_m: Optional[float] = Field(default=None, ge=0)
    # discipline -> share of the week's load, 0..1. Shares, not absolutes, so a
    # mix cannot contradict the total it is a mix of.
    discipline_mix: Dict[str, float] = Field(default_factory=dict)
    # discipline -> the week's SECONDS of it. The mix above is load, so a challenge
    # counted in time cannot be read off it; this is what it is read off. A shape
    # stored before it existed reads empty (no migration: a JSON column).
    duration_by_discipline_s: Dict[str, float] = Field(default_factory=dict)
    intent_mix: Dict[str, float] = Field(default_factory=dict)


# --- sessions --------------------------------------------------------------


class SessionStructure(BaseModel):
    """How a planned session is BUILT: its reps, its warm-up, its cool-down.

    The rep keys are the shape `workout_matching.match_planned_to_detected`
    expects, which it has compared against detected reps since the beginning of
    the project. It reads every key through `.get`, so the warm-up and cool-down
    are invisible to it rather than breaking it.

    The warm-up and cool-down are DISTANCES, in the same unit as everything else
    the runner reads (#876). They used to live in the detail prose as minutes,
    which meant the session's real length could only be recovered by multiplying
    by an assumed pace — so a 4.5 km interval session counted as its 2.4 km of
    reps. Stated as a distance, the total is addition instead of inference.

    Every field is optional, `reps_planned` included (#878). A tempo run is built
    out of a warm-up and a cool-down and no reps at all, and requiring the count
    here said the opposite: that a session has parts only when those parts are
    reps.
    """

    model_config = ConfigDict(extra="forbid")

    reps_planned: Optional[int] = Field(default=None, ge=1, le=60)
    rep_distance_m: Optional[float] = Field(default=None, gt=0)
    rest_s: Optional[float] = Field(default=None, ge=0)
    warmup_distance_m: Optional[float] = Field(default=None, gt=0)
    cooldown_distance_m: Optional[float] = Field(default=None, gt=0)


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

    # #1082. Other ways to fill this slot, recommended option first being the
    # session itself. `done_option` is which one was done: 0 the session itself,
    # n the n-th alternative; None while not done or when not recorded.
    alternatives: List[SessionAlternative] = Field(default_factory=list)
    done_option: Optional[int] = None
    # One fixed-wording note per alternative, saying what taking it instead
    # gives or costs ("+20 min toward the week's time", "kinder on the legs").
    alternative_notes: List[str] = Field(default_factory=list)

    # #1081. The day a done session used up: its matched activity's date, or the
    # day it was ticked by hand, kept inside its window. None unless done.
    done_on: Optional[date] = None
    # #1081. For an upcoming committed session that floats, the days it can
    # still go on with the rest of the week legal under every rule, done
    # sessions holding their days. None when not computed (a pinned, done or
    # suggested session, or a week the search could not settle in budget), in
    # which case the effective window is the honest fallback.
    open_days: Optional[List[date]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def planned_distance_m(self) -> float:
        """How far this session goes, decided once (#887).

        `services/schedule/planned_distance.py` is the single answer the week
        headline, the horizon and the volume ceiling all ask; the client used to
        be a fourth reader that reimplemented it instead, and the two readings
        disagreed the moment a session carried rep structure AND a duration —
        the card showed the duration and never a distance while the headline
        counted the structured kilometres from the very same row.

        Carried rather than left for the client to re-derive, the same reason
        `has_narrowed` above is. Computed rather than assigned at construction
        so no future builder of this model can forget it.

        0.0 means the session states no distance — a duration-only session is
        not converted into one, because that would be the app inventing a
        distance from an assumed pace, which `planned_distance.py` abstains
        from by design.
        """
        return _planned_distance_m(self)


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


class RunningVsNorm(BaseModel):
    """This week's RUNNING against the runner's own typical running week.

    The free-mode gauge's input. It exists because the headline is running km
    while the all-activity norm can be three times that for a runner who walks a
    lot — two different quantities in one glance. The gauge now measures the same
    thing the big number does.
    """

    model_config = ConfigDict(extra="forbid")

    typical_weekly_distance_m: float
    current_distance_m: float
    pct_vs_norm: float
    direction: str
    deadband_pct: float


class RecommendedItem(BaseModel):
    """One session on its recommended day (#1082)."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    # Its place in the day's order, or None when the order does not matter.
    order: Optional[int] = None
    # Fixed wording for why it sits where it does in the day, if anything does.
    reason: Optional[str] = None


class RecommendedDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    items: List[RecommendedItem] = Field(default_factory=list)


class DroppedSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    reason: str


class WeekRecommendationRead(BaseModel):
    """What to do each day still to come, and what moved to get here (#1082)."""

    model_config = ConfigDict(extra="forbid")

    days: List[RecommendedDay] = Field(default_factory=list)
    dropped: List[DroppedSession] = Field(default_factory=list)
    # Every move this week, in plain words, oldest first.
    changes: List[str] = Field(default_factory=list)
    # The plan's preferences as the runner reads them.
    preferences: List[str] = Field(default_factory=list)


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
    rules: List[SpacingRuleRead] = Field(default_factory=list)
    violations: List[RuleViolation] = Field(default_factory=list)
    # #1082: the recommended days for this week or a later one; None for a past
    # week, a week with no plan, or one the search could not settle.
    recommendation: Optional[WeekRecommendationRead] = None

    # The runner's own typical week, straight from the existing volume builder —
    # `norm_weekly` is ALL-ACTIVITY by that definition, with `current_runs`
    # carried alongside, so "typical" means exactly what it means on Trends.
    # Present only for the current week, where "as of today" is meaningful.
    norm: Optional[List[VolumeMetricComparison]] = None
    # The runs-only read, for the free-mode gauge. `norm` above stays as the
    # all-activity view the Trends page shows; this is the one that matches the
    # running-km headline it sits under.
    running_norm: Optional[RunningVsNorm] = None


# --- the horizon read ------------------------------------------------------


class HorizonChallenge(BaseModel):
    """One challenge's line for one horizon week.

    `threshold`, `planned` and `actual` are in the metric's own unit (seconds for
    the two time metrics, metres for distance, a count for sessions). `planned`
    is the plan's figure and, for a zone-time rule, an ESTIMATE (duration x the
    runner's own share of that activity at the zone); `actual` is MEASURED from
    heart-rate data and exists only once the week has begun. `met` is None until
    the week is over.
    """

    model_config = ConfigDict(extra="forbid")

    goal_id: UUID
    name: str
    index: int
    weeks: int
    metric: Literal["zone_time_s", "time_s", "distance_m", "sessions"]
    min_zone: Optional[int] = None
    threshold: float
    planned: Optional[float] = None
    actual: Optional[float] = None
    met: Optional[bool] = None


class HorizonWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    phase: Optional[str] = None
    # True when the week has real sessions; False when it is shape only. Derived
    # from what is actually there, so it cannot claim more than the plan holds.
    planned: bool
    # The four states a week can be in (#842). `planned`/`sketched` are the same
    # committed-session / week-shape derivation `planned` above already reads.
    # The other two are what used to be one byte-identical `else` branch: an
    # `empty` week is a genuine GAP the plan's own span covers but says nothing
    # about, while `beyond_plan` is a week past the plan's last covered week —
    # the plan never sketched it, so it must not wear the same "shape only, not
    # written yet" claim a real sketched week earns. `planned == (coverage ==
    # "planned")` always holds; see `services/schedule/horizon.py`.
    coverage: Literal["planned", "sketched", "empty", "beyond_plan"]
    is_current: bool
    running_distance_m: Optional[float] = None
    effort_score: Optional[float] = None
    # The week's long run and what its hard session is for (#980). Carried for a
    # PLANNED week (summed from its own sessions) and for a SKETCHED one (as the
    # coach stated it), so the horizon reads continuously across the boundary
    # between them instead of the progression disappearing at week four. `null`
    # for a week that holds no long run, which is a real answer and not a zero.
    long_run_distance_m: Optional[float] = None
    # The long run's time, so one given only in minutes still reads as a long
    # run (#985). Planned weeks only: a sketch states its long run as distance.
    long_run_duration_s: Optional[float] = None
    quality_focus: Optional[str] = None
    # The week's time across every activity and its walking distance (#1044):
    # summed from a planned week's committed sessions, as stated for a sketched
    # one. `null` when nothing in the week states it.
    duration_s: Optional[float] = None
    walking_distance_m: Optional[float] = None
    discipline_mix: Dict[str, float] = Field(default_factory=dict)
    intent_mix: Dict[str, float] = Field(default_factory=dict)
    # Every challenge covering this week, with the plan's figure beside the bar
    # it must clear (#1064). Empty when the runner has no season or none covers it.
    challenges: List[HorizonChallenge] = Field(default_factory=list)


class GoalRaceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    race_date: Optional[date] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    distance_m: Optional[float] = None
    target_time_s: Optional[int] = None
    notes: Optional[str] = None
    booked: bool = False
    priority: str


class ScheduleHorizonRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weeks: List[HorizonWeek] = Field(default_factory=list)
    races: List[GoalRaceRead] = Field(default_factory=list)
    has_plan: bool
    # The largest weekly load in the window; bar lengths are true proportions of
    # it, so the ramp reads honestly instead of every bar looking maxed out.
    peak_effort_score: Optional[float] = None
    # What the active plan is still short of after the retry and repair, in the
    # runner's words (#1064): said, never silently accepted.
    shortfalls: List[str] = Field(default_factory=list)


# --- goal race writes ------------------------------------------------------


class GoalRaceCreate(BaseModel):
    """A goal as the runner holds it (#1042): the date is exact, a window, or absent.

    Also the body of an edit, which replaces the whole goal: the form always
    sends every field, and booking a goal is the same goal with a date added.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    race_date: Optional[date] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    distance_m: Optional[float] = Field(default=None, gt=0, le=1_000_000)
    # Up to a week: long enough for a multi-day ultra, short enough to catch a
    # unit slip (minutes typed where seconds were meant).
    target_time_s: Optional[int] = Field(default=None, gt=0, le=7 * 24 * 3600)
    notes: Optional[str] = Field(default=None, max_length=2000)
    booked: bool = False
    priority: Literal["A", "B", "C"] = "A"

    @model_validator(mode="after")
    def _one_kind_of_date(self) -> "GoalRaceCreate":
        if self.race_date is not None and (self.window_start or self.window_end):
            raise ValueError("a goal has an exact date or a window, not both")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("a window needs both a start and an end")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("the window ends before it starts")
        if self.booked and self.race_date is None:
            raise ValueError("a booked goal has an exact date")
        if self.notes is not None:
            self.notes = self.notes.strip() or None
        return self


# --- drafting --------------------------------------------------------------


class DraftStatusRead(BaseModel):
    """Where the runner's most recent plan stands.

    `status` is None when they have never had one. `drafting` is deliberately
    invisible to the week read, so the previous plan (or free mode) keeps serving
    while a new one is written — a runner asking for a new plan never loses the
    one they are following mid-week.
    """

    model_config = ConfigDict(extra="forbid")

    status: Optional[Literal["drafting", "active", "superseded", "failed"]] = None
    plan_id: Optional[UUID] = None
    generated_at: Optional[datetime] = None
    # Runner-facing. The validator's own failure text is internal and stays in the
    # log; what the runner is owed is a plain sentence.
    message: str


class PreviousPlanRead(BaseModel):
    """The plan the runner was training to before this one (#857).

    Always an object, never a 404: "you have no earlier plan" is an answer, and
    the surface that asks needs a sentence for it either way. The
    `DraftStatusRead` precedent.

    `restorable` is the server's verdict rather than something the client works
    out from the dates. The refusal has a reason the runner is owed (a plan whose
    horizon has passed would leave them with nothing planned), and a client
    re-deriving it would eventually derive it differently from the endpoint that
    enforces it.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: Optional[UUID] = None
    # When it stopped being current, and when its thinking was written. Two
    # different facts: a plan replaced this morning can be one drafted in June.
    superseded_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    horizon_end: Optional[date] = None
    # Sessions of that plan that still lie ahead. The number that says whether
    # going back to it would actually give the runner anything.
    sessions_ahead: int = 0
    restorable: bool = False
    message: str


class AmendmentStatusRead(BaseModel):
    """Whether a confirmed amendment is being written, and how the last one ended.

    Its own read rather than a field on the week (#1003), because the runner may
    be looking at a week the amendment does not touch, and the window is what
    decides where the indicator belongs.

    `status` is None when there is nothing to say, which is both "never amended
    anything" and "the last one finished long enough ago to have expired". A
    caller must not read None as "nothing is running": the state is kept for
    minutes, and its expiry means the watching stopped, not the work.
    """

    model_config = ConfigDict(extra="forbid")

    status: Optional[Literal["working", "done", "failed"]] = None
    start: Optional[date] = None
    end: Optional[date] = None
    # What it actually did, once it has done it. The card could only name the
    # ask, so this is the first honest account of the change.
    changes: List[str] = Field(default_factory=list)
    # Runner-facing, and only on a failure.
    detail: Optional[str] = None
