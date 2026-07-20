"""
Typed schema for the coach context pack — the data structure handed to the LLM
and to the deterministic policy validator.

The pack mirrors the dict shape that build_context_pack has historically returned.
Field types are kept Optional anywhere the source row is nullable. Opaque JSONB
blobs from the analysis pipeline (efficiency_analysis, time_in_zones, etc.) are
typed as Optional[Dict[str, Any]] rather than fully expanded, because they are
not consumed by code paths within the coach module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.material import DistilledMaterial
from app.services.coach.prompt_features import PromptFeature


class ActivityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    # #650: the run's weekday as the coach's "today" anchor, so it never derives the day
    # of week from the date string. Optional so packs stored before #650 still validate.
    weekday: Optional[str] = None
    name: Optional[str]
    type: Optional[str]
    distance_m: Optional[int]
    moving_time_s: Optional[int]
    avg_hr: Optional[float]
    max_hr: Optional[float]
    avg_cadence: Optional[float]
    elev_gain_m: Optional[float]


class MetricsContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Classification axes (ADR 0007) plus the derived headline.
    headline: Optional[str]
    effort: Optional[str]
    duration_class: Optional[str]
    structure: Optional[str]
    is_hilly: Optional[bool]
    is_race: Optional[bool]
    effort_score: Optional[float]
    hr_drift: Optional[float]
    pace_variability: Optional[float]
    flags: List[str]
    confidence: str
    confidence_reasons: List[str]
    time_in_zones: Optional[Dict[str, Any]]
    zones_calibrated: bool
    zones_basis: str
    efficiency_analysis: Optional[Dict[str, Any]]
    stops_analysis: Optional[Dict[str, Any]]
    # The interval/workout group. When a session is detected these carry the structure;
    # when none is detected all three are empty and `to_serializable_dict` collapses them
    # to the single `interval_workout` signal below, so the model reads one "no workout"
    # fact instead of three null fields. Defaulted to None so the collapsed pack (which
    # omits them) re-parses; an old stored pack still carries them explicitly.
    interval_structure: Optional[Dict[str, Any]] = None
    workout_match: Optional[Dict[str, Any]] = None
    interval_kpis: Optional[Dict[str, Any]] = None
    # The collapsed signal, present in the serialized pack ONLY when no session was
    # detected (the three fields above are then omitted). None — and dropped from
    # serialization — whenever a session is present.
    interval_workout: Optional[str] = None
    risk_level: Optional[str]
    risk_score: Optional[int]
    risk_reasons: Optional[List[str]]
    training_context: Optional[Dict[str, Any]]
    # discount_signals carries the confound annotation (likely_inflated_by, reasons).
    # Its `hr_drift_pct` is a copy of `hr_drift` above (its sole home) and is dropped
    # from serialization by the one-fact-one-place fold in to_serializable_dict.
    discount_signals: Optional[Dict[str, Any]]


class CheckInContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpe: Optional[int]
    pain_score: Optional[int]
    pain_location: Optional[str]
    sleep_quality: Optional[int]
    notes: Optional[str]


class ProfileContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_type: Optional[str]
    experience_level: Optional[str]
    weekly_days_available: Optional[int]
    injury_notes: Optional[str]
    max_hr: Optional[int]
    max_hr_source: Optional[str]
    current_weekly_km: Optional[int]


class TrainingPeriodSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_count: int
    total_distance_m: int
    total_moving_time_s: int
    # round(sum(...), 1) returns int when the sum is integer-typed (empty fixture),
    # float otherwise — Union preserves the type so the cache hash stays stable.
    total_effort: Union[int, float]


class RecentTrainingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_7d: TrainingPeriodSummary
    last_28d: TrainingPeriodSummary
    previous_28d: TrainingPeriodSummary


class SafetyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    never_diagnose: bool
    pain_severe_threshold: int
    no_invented_facts: bool


class PriorReportDigest(BaseModel):
    """A token-bounded digest of one prior CoachReport (M4).

    Carries only the fields the next report needs to advance the narrative:
    when it was, its verdict label, its single strongest claim, and the
    next-steps it recommended. Deliberately excludes the full body
    (key_takeaways, thesis, risks, questions, evidence) so the pack does not
    grow with history.
    """
    model_config = ConfigDict(extra="forbid")

    activity_date: str
    headline: Optional[str]
    lead_argument: Optional[str]
    next_steps: List[str]


class BaselineTrendDelta(BaseModel):
    """The RunnerBaseline (M2) trend for THIS activity's context bucket.

    Surfaces the like-for-like longitudinal trend (effort | terrain |
    temperature band) so the coach can ground a trend claim instead of
    fabricating one. Only present when the matching bucket has enough samples
    to have stopped abstaining.
    """
    model_config = ConfigDict(extra="forbid")

    bucket: str
    sample_count: int
    efficiency_factor: Optional[Dict[str, Any]]
    hr_drift: Optional[Dict[str, Any]]


class LongitudinalContext(BaseModel):
    """M4 longitudinal contrast: the runner's own recent history.

    `prior_reports` is the digest of the last 1-2 reports (most recent first);
    `baseline_trend` is the M2 trend matching this activity's context bucket,
    or None when no comparable trend exists yet. Both empty/None for a runner's
    first ever activity.
    """
    model_config = ConfigDict(extra="forbid")

    prior_reports: List[PriorReportDigest]
    baseline_trend: Optional[BaselineTrendDelta]


class PerceivedEffortContext(BaseModel):
    """M6 perceived-vs-measured effort: the gap between what the runner felt
    (RPE) and what HR showed, plus a pain-score trend.

    `divergence` is a signed 1-5-band gap (positive = felt harder than HR
    showed); `recommended_weighting` is a symmetric, direction-driven confidence
    tilt (#654): "lead_with_felt" when it felt harder than HR showed, "dont_dismiss_hr"
    when it felt easier (hold both — real zone time is real work), "balanced" when
    aligned, or "hr_only" with no RPE. A fired confounder (`hr_confounded`) informs
    the drift read, not this weighting.
    `pain_trend` is None (no data), an abstention marker (too few samples), or a
    direction+slope trend dict scoped to this run's pain location; never a
    diagnosis. All RPE/pain fields degrade to None/empty when
    no CheckIn exists.

    One-fact-one-place fold: `rpe` and `effort_score` are the runner's reported RPE
    (== `check_in.rpe`) and the run's TRIMP-like load (== `metrics.effort_score`) —
    the same facts already in their own sections. This section's VALUE-ADD is the
    derived read (`divergence`, `divergence_direction`, `hr_confounded`,
    `recommended_weighting`), computed against the `effort_axis` intensity, not the raw
    `effort_score`. So both raw copies are DROPPED from serialization here; the coach
    reads them from `check_in`/`metrics`. Optional + default None so a dropped pack and
    a stored pre-fold pack both strict-parse; the builder still populates them on the
    model object (the derived fields are computed from `rpe`).
    """
    model_config = ConfigDict(extra="forbid")

    rpe: Optional[int] = None
    effort_axis: Optional[str]
    effort_score: Optional[float] = None
    divergence: Optional[int]
    divergence_direction: Optional[str]
    hr_confounded: bool
    recommended_weighting: str
    pain_trend: Optional[Dict[str, Any]]


class NextStepOutcome(BaseModel):
    """M7 adherence: whether one prior-report next_step appears to have landed.

    Derived deterministically from the runner's subsequent comparable activity
    (its re-derived DerivedMetric), so it costs the runner nothing. Advisory and
    auditable, never a compliance score: `label` is one of "acted_on",
    "ignored", "contradicted", or "disputed" (the runner explicitly pushed back
    on the prior advice, so `overridden` is true and the implicit read is moot).
    `basis` is a short human-readable evidence string the validator-style audit
    can read; `comparable_activity_date` anchors which run it was judged against.
    """
    model_config = ConfigDict(extra="forbid")

    prior_action: str
    theme: str
    label: str
    comparable_activity_date: Optional[str]
    basis: str
    overridden: bool


class AdherenceContext(BaseModel):
    """M7 adherence learning loop: did the runner act on the LAST report's advice?

    Re-derived each run from already-stored prior reports + subsequent
    Activity/DerivedMetric rows (compute-on-demand, no durable store; M8 owns the
    gated belief store). `prior_report_date` is the date of the source report's
    activity. `outcomes` carries only next_steps that mapped to a recognised
    theme AND had a comparable, non-noisy subsequent run to judge against;
    everything else abstains and is simply absent, so an empty list means "no
    adherence signal to report" and the coach says nothing about it.
    """
    model_config = ConfigDict(extra="forbid")

    prior_report_date: Optional[str]
    outcomes: List[NextStepOutcome]


class BelievedFact(BaseModel):
    """One durable belief retrieved from the M8 CoachingContext store.

    Carries the human-readable `statement` the coach applies, plus the
    confidence/recency tags it must hedge against: `confidence` (low/medium/high,
    grows with independent observations), `observed_count`, and
    `last_seen_days_ago`. A belief is contrast/prior context only and never
    overrides this run's re-derived DerivedMetric.
    """
    model_config = ConfigDict(extra="forbid")

    kind: str
    statement: str
    confidence: str
    observed_count: int
    last_seen_days_ago: int


class BelievedFactsContext(BaseModel):
    """M8 belief store retrieval: the runner-model accumulated from prior reports.

    `facts` holds the active, non-decayed, quality-cleared beliefs (HR confounds,
    adherence patterns) most-confident-first, bounded. Empty for a runner with no
    accumulated beliefs yet (the coach then has no priors to apply).
    """
    model_config = ConfigDict(extra="forbid")

    facts: List[BelievedFact]


class CalibrationContext(BaseModel):
    """M9 self-calibrating correction + non-diagnostic referral.

    `hr_drift` reads this run's HR drift against the runner's OWN typical drift
    for these conditions when enough comparable history exists (`calibrated`
    true), else carries the labeled population heuristic fallback. `referral` is
    a non-diagnostic clinician nudge for a computable red-flag pattern, or None;
    it is templated, pipeline-owned, and never names a condition or diagnoses.
    Neither overrides the re-derived DerivedMetric.

    One-fact-one-place fold: `hr_drift.observed_drift_pct` is the run's own HR drift
    (== `metrics.hr_drift`), so it is DROPPED from serialization here; the section's
    value-add is the calibrated comparison (`expected_drift_pct`, `delta_pct`,
    `comparison`, `basis`), and the coach reads the raw drift from `metrics.hr_drift`.
    `hr_drift` is a freeform dict, so dropping the key needs no schema change and a
    stored pre-fold pack still validates.
    """
    model_config = ConfigDict(extra="forbid")

    hr_drift: Dict[str, Any]
    referral: Optional[Dict[str, Any]]


class PreferenceTheme(BaseModel):
    """One advice theme in the M10 preference profile, with the runner's measured
    tendency to act on it (`acts_on` / `mixed` / `ignores`) and the acted/total
    counts it was derived from."""
    model_config = ConfigDict(extra="forbid")

    theme: str
    tendency: str
    acted: int
    total: int


class PreferenceProfile(BaseModel):
    """M10 per-runner preference profile: which kinds of advice this runner
    demonstrably acts on, derived from the accumulated M7/M8 adherence record (so
    it already reflects explicit pushback, which never reinforced those beliefs).
    The coach uses it to RERANK and FRAME next_steps toward what lands, never to
    override the re-derived DerivedMetric or fabricate advice. Empty `themes` for a
    runner without enough adherence history yet.
    """
    model_config = ConfigDict(extra="forbid")

    themes: List[PreferenceTheme]


class NarrativeContext(BaseModel):
    """A2c durable-memory NARRATIVE: the bounded per-runner relationship story.

    VOICE ONLY. This is the LLM-written half of durable memory (ADR 0008) — the
    arc of the coaching relationship so far, the tone that lands, the open
    threads — re-grounded from the deterministic facts by a background
    Consolidation job. The authority boundary is absolute and mirrors the belief
    rule: the narrative can never override a re-derived `DerivedMetric` or a
    deterministic fact, and can never be the cited source of a factual claim. It
    is colour, not data.

    `narrative` is None until the Consolidation job has written one (the first
    exchange for a runner has no narrative yet, exactly as a new runner has no
    beliefs). The provenance tags let the coach hedge a thin or stale story:
    `source_report_count` is how many exchanges it was grounded from, and
    `last_updated_days_ago` is how long before this run it was last re-grounded.
    """
    model_config = ConfigDict(extra="forbid")

    narrative: Optional[str] = None
    source_report_count: Optional[int] = None
    last_updated_days_ago: Optional[int] = None


class NoveltyContext(BaseModel):
    """A4 salience substrate — the deterministic novelty signal.

    `first_of_kind` lists which axes of THIS run are first-of-their-kind in the
    runner's analysed history (e.g. "first_interval_session", "first_long_run",
    "first_race", "first_hilly_run"), computed read-time against the full
    `is_deleted == False` history. `has_history` is False until the runner has
    enough prior analysed runs for novelty to be meaningful (a cold-start runner's
    first runs are trivially "first of everything", so the signal abstains rather
    than flagging them all). Advisory input to the opener LLM's depth judgment,
    never a force. Salience is NOT intensity or load (CONTEXT.md): novelty keys on
    axis-novelty only, never on effort_score magnitude.
    """
    model_config = ConfigDict(extra="forbid")

    first_of_kind: List[str] = Field(default_factory=list)
    has_history: bool = False


class SafetyOverride(BaseModel):
    """A4 salience substrate — the deterministic safety override.

    A red-flag pattern (the same predicate as the M9 referral nudge:
    `illness_or_extreme_fatigue` in flags, or sustained notable pain) forces a
    non-silent opener and a scheduled fuller turn regardless of the LLM's
    judgment — the model can never decide to stay quiet on a safety signal.
    `force_fuller` is the authority bit the opener job's scheduling reads;
    `reasons` are the matched red-flag strings. Abstains (force_fuller False,
    reasons empty) when no red flag is present.
    """
    model_config = ConfigDict(extra="forbid")

    force_fuller: bool = False
    reasons: List[str] = Field(default_factory=list)


class SalienceContext(BaseModel):
    """A4 salience facts assembled into the (lean) opener context and the fuller
    pack: the deterministic novelty signal plus the deterministic safety override.

    Salience is HYBRID (ADR 0010): these deterministic facts — plus the existing
    baseline/flag/adherence signals already in the pack — feed the opener LLM's
    judgment of depth, tone, and whether to deepen; the safety override is the
    only deterministic force. There is no monolithic salience score.
    """
    model_config = ConfigDict(extra="forbid")

    novelty: NoveltyContext = Field(default_factory=NoveltyContext)
    safety_override: SafetyOverride = Field(default_factory=SafetyOverride)


class ContinuityContext(BaseModel):
    """A4 fuller-turn continuity: what the opener already said and any reply.

    `opener_message` is the stage-one prose (so the fuller turn ADVANCES it
    rather than restating); `reply` is a chat reply the runner sent after the
    opener (the check-in, if any, is already in `check_in`). Both empty for the
    opener stage and for a single-shot exchange, so the fuller-mode prompt simply
    has no prior turn to build on.
    """
    model_config = ConfigDict(extra="forbid")

    opener_message: Optional[str] = None
    reply: Optional[str] = None


class BlockMember(BaseModel):
    """One activity in a multi-member Block, as the coach sees it."""
    model_config = ConfigDict(extra="forbid")

    type: str
    duration_s: int
    distance_m: int
    is_primary: bool = False


class BlockContext(BaseModel):
    """A1 (ADR 0011): the thin block aggregate for a MULTI-member block — the
    member list (chronological) plus combined totals, so the coach can speak
    about the morning as a whole. The subject activity stays the block's
    primary; per-activity signals are unchanged. Never built for a
    block-of-one (AC8: the solo-run pack is byte-stable, no `block` key)."""
    model_config = ConfigDict(extra="forbid")

    members: list[BlockMember]
    combined_duration_s: int
    combined_distance_m: int


class CorpusSchoolContext(BaseModel):
    """P1.2 (ADR 0014): the keyed school of training thought in the corpus section.

    Short structured fields mirroring the code-resident `corpus.School` — the
    school's one-line `stance`, its `principles`, how it frames methods
    (`method_framing`), and what it foregrounds (`emphasis_hints`). None when no
    school resolves (the house-core-only degradation lives on CorpusContext)."""
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    stance: str
    principles: List[str]
    method_framing: str
    emphasis_hints: List[str]


class CorpusContext(BaseModel):
    """P1.2 (ADR 0014): the coaching CORPUS the coach grounds its JUDGMENT in.

    The always-present house `house_principles` plus the keyed `school` (None =
    house-core-only). This is judgment knowledge the coach reasons over — NEVER a
    fact and never grounding (validator rule 7 rejects citing a `corpus.*` path as
    evidence; prompt rule 25 keeps it from overriding the re-derived DerivedMetric
    or the safety floor). It rides the pack like `narrative` and is emitted ONLY
    under a corpus-aware prompt id, so the pack stays byte-stable otherwise.

    `user_materials` (P4, #286) is the runner's own ACTIVE distilled materials (the
    strict `DistilledMaterial` shape), carrying the hardest Authority tiering tier —
    they beat house philosophy for stance (prompt rule 28) — yet are still judgment
    reference, NEVER fact (validator rule 8 rejects citing a `corpus.user_materials.*`
    path) and NEVER instructions. It is None under every non-user-materials prompt
    and DROPPED from serialization when None, so the corpus section is byte-identical
    to its P1.2/P1.3 shape under v4/v5/v6 (the activation boundary: materials take
    effect only under v7); under v7 it is a list (empty when the runner has none)."""
    model_config = ConfigDict(extra="forbid")

    house_principles: List[str]
    school: Optional[CorpusSchoolContext] = None
    user_materials: Optional[List[DistilledMaterial]] = None


class StanceEmphasisAxis(BaseModel):
    """P1.3 (ADR 0015): one of the runner's two operable emphasis axes.

    Carries the axis poles, the runner's 1-5 setting, and a short descriptor — the
    structured form the coach reads to know what to FOREGROUND. Runner PREFERENCE,
    never evidence and never grounding (the emphasis reweights what the coach leads
    with, never the facts or the safety floor; prompt rule 26)."""
    model_config = ConfigDict(extra="forbid")

    key: str
    low_pole: str
    high_pole: str
    value: int
    descriptor: str


class StanceContext(BaseModel):
    """P1.3 (ADR 0015): the runner's two emphasis axes (Data↔Sentiment,
    Process↔Outcome) — the operable half of `Coaching stance`.

    The runner's SELECTED SCHOOL is NOT here — it rides the `corpus` section (the
    school's principles/method-framing). This section carries only the emphasis
    tilt, kept separate because the corpus is coaching KNOWLEDGE while emphasis is
    runner PREFERENCE. It rides the pack like `corpus` and is emitted ONLY under a
    stance-aware prompt id, so the pack stays byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    emphasis: List[StanceEmphasisAxis]


class TrainingLoadContext(BaseModel):
    """P3 (ADR 0016): the runner's current-condition readiness read — our own
    deterministic EWMA fitness/fatigue/form model from the per-activity load
    primitive (`effort_score`), computed at read time as of the subject activity.

    `fitness` (chronic load), `fatigue` (acute load), and `form` (their balance)
    are in Edwards zone-minutes; `condition`/`trend` are the labelled read the coach
    reasons over. This is a tier-3 deterministic FACT the coach may cite (unlike the
    voice-only `narrative` and judgment-only `corpus`), but per `Authority tiering`
    it never overrides this run's re-derived DerivedMetric or the safety floor
    (prompt rule 27). While `warming_up`, the chronic baseline is not yet
    established and the read abstains from a confident verdict. Emitted ONLY under a
    training-load-aware prompt id, so the pack stays byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    fitness: float
    fatigue: float
    form: float
    ramp_rate: float
    condition: str          # fresh | balanced | fatigued | overreaching | building_baseline
    trend: str              # building | steady | detraining
    ramp_aggressive: bool
    warming_up: bool
    sample_count: int


class VolumeMetricComparison(BaseModel):
    """#400: one training metric's current window value against the runner's norm.

    `current_all` is the holistic value (every logged activity — runs, walks, rides,
    rowing, weights), the cardio view the runner reasons in; `current_runs` is the
    runs-only figure alongside, so the coach can speak to "12 sessions, 4 of them
    runs" and never read a walk as a run. The norm is per-week, all-activities,
    computed over history BEFORE the current 7 days: `norm_weekly` over ~12 weeks
    (the stable baseline), `norm_weekly_recent` over ~4 weeks (recent context).
    `direction`/`direction_recent` are the labelled read against each norm with a
    deadband, so a deliberate easy week reads as `down` rather than alarming. A
    deterministic FACT; it never overrides this run's re-derived DerivedMetric.

    Fold (one lane per fact): `current_all`/`current_runs` are Optional and are
    DROPPED from serialization on the `rolling_7d` window — but ONLY when a
    `recent_training` section is present to carry the numbers (recent-training-aware
    prompts, v11+). That window spans the same trailing 7 days as
    `recent_training.last_7d`, so its raw totals were a second copy of that section's
    roll-up/by_type. `rolling_7d` then carries the vs-norm VERDICT (norm + direction +
    pct); the actual trailing-7d numbers live in `recent_training`. Under v9/v10 (no
    descriptive lane) `rolling_7d` KEEPS them. The `calendar_week` framing always KEEPS
    them (it has no counterpart elsewhere). The builder still computes both
    (direction/pct derive from them); Optional + default None so a serialized-and-
    dropped pack — and a stored pre-fold pack — both strict-parse."""
    model_config = ConfigDict(extra="forbid")

    metric: str  # sessions | distance_m | moving_time_s | effort_score
    current_all: Optional[Union[int, float]] = None
    current_runs: Optional[Union[int, float]] = None
    norm_weekly: Optional[float] = None
    norm_weekly_recent: Optional[float] = None
    pct_vs_norm: Optional[float] = None
    direction: str          # up | in_line | down | no_norm
    direction_recent: str   # up | in_line | down | no_norm


class VolumeWindow(BaseModel):
    """#400: one window framing of the volume-vs-norm read.

    `rolling_7d` is the trailing 7 days (a full week, directly comparable to the
    per-week norm). `calendar_week` is the current Monday-Sunday block to date — the
    framing runners plan in — with `days_elapsed` (1-7) and `complete` (only on
    Sunday); its direction compares the week-to-date against the norm PRO-RATED to
    the same elapsed days, so a partial week is judged fairly."""
    model_config = ConfigDict(extra="forbid")

    window: str          # rolling_7d | calendar_week
    days_elapsed: int
    complete: bool
    metrics: List[VolumeMetricComparison]


class TrainingVolumeContext(BaseModel):
    """#400: the frequency-/volume-vs-norm signal, both framings.

    Answers "is this week up, down, or normal for me" per metric, so the coach
    reads a deliberate down week as intentional rather than worrying. `has_baseline`
    is False when history is too thin to establish a norm (every direction is then
    `no_norm`). Emitted ONLY under a volume-aware prompt id, so the pack stays
    byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    rolling_7d: VolumeWindow
    calendar_week: VolumeWindow
    baseline_weeks: int
    baseline_weeks_recent: int
    has_baseline: bool


class RecentTypeBreakdown(BaseModel):
    """#444: one activity type's contribution to a recent-training window — counts,
    per-type distance/time/load totals, and the type's SHARE of the window's sessions
    (the modality mix as a precomputed number, so a walk is never read as a run)."""
    model_config = ConfigDict(extra="forbid")

    type: str
    count: int
    distance_m: int
    moving_time_s: int
    effort_score: float
    share_pct: float  # this type's share of the window's session COUNT (0-100)


class RecentActivityItem(BaseModel):
    """#444: one session in the recent (7d) window — its type and intensity/load
    read, so the coach can speak to specific sessions, not only aggregates."""
    model_config = ConfigDict(extra="forbid")

    date: str
    type: str
    effort: Optional[str]  # HR-derived intensity axis (recovery|easy|moderate|tempo|hard)
    effort_score: Optional[float]  # the TRIMP-like LOAD number (grows with duration)
    # #647: per-run volume, so the coach can read a long run vs a short one rather than
    # only aggregates. Optional so packs stored before #647 (a populated per-session list
    # without these keys) still validate under extra="forbid" on strict re-parse.
    distance_m: Optional[int] = None
    moving_time_s: Optional[int] = None
    # #650: the session's weekday as GIVEN data (never derived from a date string), its
    # structure, and a compact rep shape for interval sessions — so the coach can place
    # the session in the week and recognise past rep work without asking. All Optional so
    # packs stored before #650 still validate on strict re-parse.
    weekday: Optional[str] = None
    structure: Optional[str] = None  # continuous | intervals (the classifier verdict)
    interval_shape: Optional[str] = None  # e.g. "7x400m"; None for a continuous run
    # #661: how this past session's interval structure was detected — "recorded_laps"
    # when the runner pressed the lap button (so a later report must NOT advise the lap
    # button on this session), else None (stream-detected or not an interval session).
    # Reuses the exact `metrics.interval_structure.source` vocabulary the subject-run
    # rule already keys on, so the coach reads a past session the same way. Optional and
    # null-dropped, so packs stored before #661 still validate on strict re-parse.
    source: Optional[str] = None
    # #650: the classifier's runner-RELATIVE "long run" verdict, surfaced only when long
    # (None otherwise) so the coach recognises a past long run without guessing a
    # per-runner distance threshold. The symmetric case to the interval marker.
    long_run: Optional[bool] = None


class RecentComparison(BaseModel):
    """#444: one metric's current window value with its vs-TYPICAL and vs-PREV
    comparison, ALL percentages precomputed and each carrying a self-describing
    BASIS so the coach never cites a comparison whose reference it does not know.

    `current_all` is holistic (every logged activity); `current_runs` is runs-only.
    "typical" is the runner's own per-day rate over a trailing baseline (the Trends
    definition, #444 decision 1); "prev" is the equal-length window immediately
    before this one. A deterministic FACT the coach may cite; never an intensity
    verdict (continuity with the load-vs-intensity rule) and never overrides the
    run's re-derived DerivedMetric or the safety floor."""
    model_config = ConfigDict(extra="forbid")

    metric: str  # sessions | distance_m | moving_time_s | effort_score
    # Pack trim: current_all/current_runs are no longer emitted per row — they duplicate
    # the window's own roll-up (current_all == total_*/activity_count, current_runs ==
    # the by_type["Run"] entry). (training_volume.rolling_7d no longer carries current
    # either — that overlap was folded out there too; recent_training is the one home
    # for the trailing-7d numbers.) Kept Optional so a pre-trim stored pack
    # (extra="forbid") still validates; dropped from serialization when None.
    current_all: Optional[Union[int, float]] = None
    current_runs: Optional[Union[int, float]] = None
    vs_typical_pct: Optional[float] = None
    # Optional/None on the 7d window, which carries no vs-typical read (#451: its weekly
    # vs-norm verdict lives in `training_volume`). A real direction on the 30d window.
    # Dropped from serialization when None.
    vs_typical_direction: Optional[str] = None  # up | in_line | down | no_norm
    vs_prev_pct: Optional[float] = None
    vs_prev_direction: str     # up | in_line | down | no_norm
    # Pack trim: the self-describing basis strings moved UP to the window level (one copy
    # per window, not one per metric row — they were identical across all four). Kept
    # Optional here so a pre-trim stored pack still validates; dropped when None.
    typical_basis: Optional[str] = None
    prev_basis: Optional[str] = None


class RecentTrainingWindow(BaseModel):
    """#444: one window's modality-aware roll-up — the per-type breakdown + share and
    overall totals, plus (7d/30d only) the vs-typical/vs-prev comparisons; the 7d
    window also carries the bounded per-activity list (the longer windows do not)."""
    model_config = ConfigDict(extra="forbid")

    window: str  # last_7d | last_30d | previous_30d
    days: int
    activity_count: int
    by_type: List[RecentTypeBreakdown]
    total_distance_m: int
    total_moving_time_s: int
    total_effort: float
    comparisons: List[RecentComparison]   # 7d/30d only; empty for previous_30d
    activities: List[RecentActivityItem]  # 7d only; empty for the longer windows
    # Pack trim: the comparison basis strings live once per window now (they were
    # identical across every metric row). `prev_basis` is set on any window that carries
    # comparisons; `typical_basis` only on the window with a vs-typical read (30d). Both
    # Optional (absent on previous_30d and on pre-trim stored packs); dropped when None.
    prev_basis: Optional[str] = None
    typical_basis: Optional[str] = None


class RecentTrainingContext(BaseModel):
    """#444: the modality-aware recent-training picture — the rich successor to
    `recent_training_summary` (per-type breakdown + per-activity detail + vs-typical/
    vs-prev with self-describing basis). Emitted ONLY under a recent-training-aware
    prompt id, so the pack stays byte-stable otherwise. `has_baseline` is False when
    history is too thin for any vs-typical norm (every direction then `no_norm`)."""
    model_config = ConfigDict(extra="forbid")

    last_7d: RecentTrainingWindow
    last_30d: RecentTrainingWindow
    # Optional (#522) so COACH_PREVIOUS_30D_ENABLED can drop this one window; the
    # vs-prev comparisons on last_7d/last_30d are computed independently and are
    # unaffected. None is dropped from serialization by _drop_recent_training_dedup.
    previous_30d: Optional[RecentTrainingWindow] = None
    has_baseline: bool


class TrainingHistoryTypeShare(BaseModel):
    """ADR 0026 Slice 2 (#670): one activity type's weekly rate + session share within a
    training-history bucket. Present only on the recent-weeks-bounded (grouped_v2) ladder,
    so the coach never reads a blended distance total as running mileage (a 171 km ski week
    is a `BackcountrySki` row, not run distance)."""
    model_config = ConfigDict(extra="forbid")

    type: str
    avg_weekly_distance_m: int
    avg_weekly_sessions: float
    share_pct: float  # share of the bucket's sessions that are this type


class TrainingHistoryBucket(BaseModel):
    """#561: one coarse, far-horizon volume bucket in the level-of-detail ladder.

    The deep history reaches the coach at DECAYING resolution: the recent window owns
    the detailed near term in full (under the original ladder `recent_training`'s
    last_7d/last_30d/previous_30d all live within 0-60d; under the ADR 0026 Slice 2
    grouped_v2 ladder `recent_weeks` owns the last ~2 weeks day by day), so this ladder
    starts AFTER that and widens as it goes back, each bucket reporting an AVERAGE WEEKLY
    rate so buckets of unequal width stay directly comparable (the runner's own
    "20 km/week" read). The weekly average divides by the weeks the bucket actually spans
    WITHIN the runner's history (so a partially-covered bucket is not deflated), mirroring
    the volume.py clamp. A bucket is emitted only when it holds real data, so the ladder
    self-sizes to how far the history reaches.

    The four `*_load`/`by_type`/`from_date`/`to_date` fields ride ONLY the grouped_v2
    ladder (Optional-None on the original 60d ladder and surgically dropped there, so every
    prior prompt is byte-identical): a modality-agnostic weekly LOAD rate, the per-type
    split (so cross-training is never read as running), and explicit calendar bounds for
    the relative label."""
    model_config = ConfigDict(extra="forbid")

    label: str            # human horizon, e.g. "2-6 months ago"
    start_days_ago: int   # inclusive age lower bound
    end_days_ago: int     # exclusive age upper bound (== start of the open tail's coverage end)
    weeks: float          # weeks spanned within history (the averaging denominator)
    avg_weekly_distance_m: int
    avg_weekly_sessions: float
    run_share_pct: float  # share of this bucket's sessions that are runs (modality mix)
    # ADR 0026 Slice 2 (#670): grouped_v2-only enrichments (dropped when None, byte-stable).
    from_date: Optional[str] = None   # oldest calendar edge, month-year e.g. "Jul 2025"
    to_date: Optional[str] = None      # newest calendar edge, month-year e.g. "Jan 2026"
    avg_weekly_load: Optional[int] = None  # effort_score/week (modality-agnostic training load)
    by_type: Optional[List["TrainingHistoryTypeShare"]] = None  # per-type split, most-frequent-first


class TrainingHistoryTraits(BaseModel):
    """#561: the durability traits a coach reads from accumulated history — the
    eval-able headline that tells a long-tenured low-volume runner apart from a
    short-tenured high-volume one (near-identical lifetime totals, very different
    athletes). Every trait is a deterministic FACT the coach may cite; none is an
    intensity verdict and none overrides this run's re-derived DerivedMetric or the
    safety floor. Comparisons abstain (`no_norm` / null) when history is too thin to
    resolve them."""
    model_config = ConfigDict(extra="forbid")

    training_age_years: float                        # first activity to as_of
    peak_sustained_weekly_distance_m: int            # highest rolling 4-week avg weekly distance, all history
    # The runner's current weekly volume is NOT restated here — it lives once in
    # `recent_training.last_30d` (#451 one-lane). This carries only the RELATIONSHIP
    # to the peak (the new durability signal), which the coach reads as "near / below
    # your historical ceiling" without a second copy of the recent-volume number.
    current_vs_peak_pct: Optional[float] = None      # last-4wk rate / peak * 100; None when peak is 0
    trajectory_direction: str                        # up | in_line | down | no_norm (recent 12mo vs prior 12mo)
    trajectory_pct: Optional[float] = None           # recent-12mo vs prior-12mo weekly rate
    time_at_current_load_years: Optional[float] = None  # span the trailing-4wk rate stayed within a band of current
    # ADR 0026 Slice 2 (#670): the modality-agnostic LOAD durability read, riding ONLY the
    # grouped_v2 ladder (Optional-None + surgically dropped on the original ladder, so every
    # prior prompt is byte-identical). effort_score is comparable across modalities, so a
    # ski/bike block still counts as sustained training even when running distance is low.
    peak_sustained_weekly_load: Optional[int] = None      # highest rolling 4-week avg weekly effort_score
    current_vs_peak_load_pct: Optional[float] = None      # last-4wk load rate / peak load * 100; None when peak load is 0


class TrainingHistoryContext(BaseModel):
    """#561: the multi-year training-history picture — a richness-decaying volume
    ladder (`timeline`) plus the durability `traits`. Emitted ONLY under a
    training-history-aware prompt id, and ONLY when the runner has enough history
    beyond the recent window to describe (else the builder returns None and the
    section is dropped from serialization, byte-stable elsewhere). The timeline is
    newest-bucket-first."""
    model_config = ConfigDict(extra="forbid")

    traits: TrainingHistoryTraits
    timeline: List[TrainingHistoryBucket]


class IntensityBandShare(BaseModel):
    """#578: an easy/moderate/hard split as percentages summing to ~100 (rounding). Used
    both for the time split WITHIN one run and the session-count share across a window."""
    model_config = ConfigDict(extra="forbid")

    easy_pct: float
    moderate_pct: float
    hard_pct: float


class IntensitySession(BaseModel):
    """#578: this run's intensity read — its dominant band (collapsed from the HR-derived
    `effort` axis: recovery/easy -> easy, moderate -> moderate, tempo/hard -> hard) plus,
    when zone time is present, the easy/moderate/hard split of time WITHIN the run, and
    whether a discount signal (heat/hills/stimulant) fired on it."""
    model_config = ConfigDict(extra="forbid")

    band: Optional[str] = None                       # easy | moderate | hard; None without HR
    within_run: Optional[IntensityBandShare] = None  # time-in-zone split inside this run; None without zones
    hr_confounded: bool = False                      # a fired discount signal exculpates apparent hardness


class IntensityContext(BaseModel):
    """#578: the deterministic intensity-distribution-and-trend signal — the data-layer
    half of the v13 memory addendum's "read training direction from the data" discipline.

    Carries THIS run's intensity (`this_session`) and the runner's RECENT distribution +
    trend: the easy/moderate/hard share of recent comparable sessions (session-count over
    `window_days`), both raw and confounder-EXCULPATED (`distribution_adjusted`, where a
    session whose HR drift fired a discount signal does not count its apparent hardness),
    plus whether this run is harder/easier than recent (`this_run_vs_recent`) and whether
    the distribution is shifting (`trend_direction`, recent vs prior equal window's
    exculpated hard-share). Every figure is a deterministic FACT the coach may cite; none
    is an intensity verdict and none overrides this run's re-derived DerivedMetric or the
    safety floor. Emitted ONLY under an intensity-aware prompt id; abstains internally
    (null distributions, `no_norm` directions) when history is thin, like volume/
    recent_training, and the whole section is dropped (builder returns None) only when
    there is nothing to say."""
    model_config = ConfigDict(extra="forbid")

    this_session: IntensitySession
    window_days: int
    session_count: int                                  # comparable recent sessions (excl. this run, races, no-HR)
    distribution: Optional[IntensityBandShare] = None           # raw session-count share
    distribution_adjusted: Optional[IntensityBandShare] = None  # confounder-exculpated share
    confounded_session_count: int = 0
    this_run_vs_recent: str                             # easier | in_line | harder | no_norm
    trend_direction: str                                # easier | in_line | harder | no_norm
    trend_hard_share_delta_pct: Optional[float] = None  # recent minus prior exculpated hard-share (pct points)
    prior_session_count: int = 0
    has_distribution: bool = False


# --- ADR 0026 Slice 3 (#673): the merged this-run intensity read -------------
# "How hard was this run, really?" was answered in four sibling sections
# (`metrics.effort`, `perceived_effort`, `calibration.hr_drift`, `intensity.this_session`
# + `metrics.discount_signals`), inviting the model to narrate each in isolation (the
# #636 misread). Slice 3 collapses the THIS-RUN lenses into one `this_run.intensity_read`
# and moves the RECENT distribution/trend half to `right_now.intensity_mix` (a runner-
# state fact, not a this-run fact). Both ride the pack as NEW gated sections behind new
# PromptFeatures (INTENSITY_READ / INTENSITY_MIX), so under every prior prompt the old
# `perceived_effort`/`calibration`/`intensity` sections still emit byte-identically; the
# new grouped prompt carries the two new features INSTEAD of INTENSITY and retires those
# three (perceived_effort/calibration drop to None; the combined intensity section drops
# with its feature). The confounder LEADS the read and links to the drift, so an elevated
# drift on a hot day is never narrated as fatigue.


class IntensityFeltVsMeasured(BaseModel):
    """The RPE-vs-HR read (M6), reframed as a comparison. `read` is the signed 1-5-band
    divergence direction; `trust` is which signal to weight (RPE survives HR distortion
    under a confounder). Omitted from the read entirely when there is no check-in (no RPE
    to compare)."""
    model_config = ConfigDict(extra="forbid")

    read: str                                 # aligned | felt_harder | felt_easier
    trust: str                                # lead_with_felt | dont_dismiss_hr | balanced (#654)


class IntensityDriftRead(BaseModel):
    """This run's HR drift read against the runner's OWN typical for these conditions (M9).
    `personal_norm` is False when there are too few comparable runs (`typical_pct` is then
    null and `read` compares against the general ~5% guideline). `confounded` is set only
    when a discount signal fired, so the coach reads an elevated drift as likely inflated
    by the conditions rather than as fatigue. Omitted when no drift was measured."""
    model_config = ConfigDict(extra="forbid")

    observed_pct: float
    typical_pct: Optional[float] = None       # your norm for these conditions; null when < 4 comparable runs
    read: str                                 # above | below | in_line
    personal_norm: bool                       # True = your own norm; False = the general ~5% rule of thumb
    confounded: Optional[bool] = None         # True when a confounder likely inflated it; dropped when None
    basis: str


class IntensityRead(BaseModel):
    """ADR 0026 Slice 3 (#673): the single "how hard was this run, really" read, merging
    the HR intensity band, the within-run time split, the felt-vs-measured (RPE) gap, the
    drift-vs-your-typical comparison, and the confounders that fired. `confounders` LEADS
    the block (heat/hills/stimulant), so the coach reads the HR-derived facts below through
    that frame; `vs_recent` is this run against the runner's recent 4-week intensity norm,
    computed confounder-SYMMETRICALLY (this run's own band is exculpated the same way the
    recent baseline is). Every sparse field is Optional and dropped from serialization when
    it does not apply, so a bare indoor run stays lean. Emitted ONLY under an
    intensity-read-aware prompt id, so the pack stays byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    confounders: List[str] = Field(default_factory=list)  # heat | hills | stimulant; dropped when empty
    band: Optional[str] = None                            # easy | moderate | hard (collapsed HR effort axis)
    within_run: Optional[IntensityBandShare] = None       # time-in-zone split; None without zone data
    felt_vs_measured: Optional[IntensityFeltVsMeasured] = None  # None without a check-in
    drift_vs_typical: Optional[IntensityDriftRead] = None       # None when no drift measured
    vs_recent: str                                        # easier | in_line | harder | no_norm


class IntensityMix(BaseModel):
    """ADR 0026 Slice 3 (#673): the RECENT intensity distribution + trend — the "how hard
    has this runner been training lately" half, a runner-state fact that sits with the
    other `right_now` reads. The distribution is the confounder-EXCULPATED session-count
    share over the recent window (a hot week is not misread as genuine hard volume). The
    builder returns None (section dropped) when history is too thin for a distribution, so
    the section appears only when there is a real recent read. Emitted ONLY under an
    intensity-mix-aware prompt id, so the pack stays byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    window_days: int
    sessions: int                             # comparable recent sessions (excl. this run, races, no-HR)
    distribution: IntensityBandShare          # confounder-exculpated easy/moderate/hard share
    trend: str                                # easier | in_line | harder | no_norm (recent vs prior window)


class MemoryContext(BaseModel):
    """ADR 0025: the runner memory profile surfaced WHOLE — the five capped sections
    of the runner's STATED facts + soft character, plus provenance so the coach can
    hedge a thin/stale profile. Emitted ONLY under a memory-aware prompt id, and
    ONLY when a profile row exists (else the builder returns None and the section is
    dropped from serialization, byte-stable elsewhere). It is the citable stated tier
    (the memory addendum): the coach may reference it, but it yields to this run's
    re-derived DerivedMetric on a factual conflict and never lowers the safety floor;
    it carries no behavioral verdict (those are re-derived live, never stored)."""
    model_config = ConfigDict(extra="forbid")

    who_you_are: List[str] = []
    limits_and_constraints: List[str] = []
    goals_and_plans: List[str] = []
    what_works_for_you: List[str] = []
    lately: List[str] = []
    # Provenance, for hedging a thin/stale profile (mirrors NarrativeContext).
    last_updated_days_ago: Optional[int] = None
    source_report_count: Optional[int] = None


# --- ADR 0026 Slice 2 (#670): readiness + recent_weeks ----------------------
# The `right_now` group's content redesign: `training_load` is renamed `readiness`
# (identical content, coaching-question name) and `training_volume` + `recent_training`
# MERGE into one day-resolved `recent_weeks` read on one week model. Both ride the pack
# as NEW gated sections behind new PromptFeatures (READINESS / RECENT_WEEKS), so under
# every prior prompt (prod lean_v1, grouped_v1) the old three sections still emit,
# byte-identically; a new grouped prompt carries the two new features INSTEAD of the
# three old ones. The two shapes coexist in `RightNow`; only one set serializes per
# prompt (the Optional-and-drop idiom).


class ReadinessContext(BaseModel):
    """ADR 0026 Slice 2: the runner's current-condition readiness read, RENAMED from
    `training_load` to the coaching-question key `right_now.readiness`. Byte-for-byte the
    same content as TrainingLoadContext — our own deterministic EWMA fitness/fatigue/form
    model computed as of THIS run (so the run is in the acute load). A tier-3
    deterministic FACT the coach may cite, but it never overrides the run's re-derived
    DerivedMetric or the safety floor. Emitted ONLY under a readiness-aware prompt id, so
    the pack stays byte-stable otherwise."""
    model_config = ConfigDict(extra="forbid")

    fitness: float
    fatigue: float
    form: float
    ramp_rate: float
    condition: str          # fresh | balanced | fatigued | overreaching | building_baseline
    trend: str              # building | steady | detraining
    ramp_aggressive: bool
    warming_up: bool
    sample_count: int


class RecentWeeksActivity(BaseModel):
    """ADR 0026 Slice 2: one session inside a recent-weeks day. Carries the SHAPE of the
    session (type/distance/duration/intensity/structure) plus the honest felt-vs-measured
    signals the coach reads together — `intensity` is the HR-derived effort axis, `rpe`
    is how it actually felt (the corrective to an HR band that a stimulant/heat can
    inflate), `avg_hr` is the raw HR, `load` is the TRIMP-like effort_score. Every field
    but `type` is Optional and DROPPED from serialization when it does not apply (a
    no-HR/indoor session, a non-run, a run without a check-in), so a rich outdoor run and
    a bare indoor ride both stay lean."""
    model_config = ConfigDict(extra="forbid")

    type: str
    distance_km: Optional[float] = None
    duration: Optional[str] = None        # H:MM:SS moving time
    intensity: Optional[str] = None       # HR-derived effort axis (recovery|easy|moderate|tempo|hard)
    avg_hr: Optional[float] = None
    rpe: Optional[int] = None             # felt effort, from the check-in
    load: Optional[float] = None          # == effort_score (TRIMP-like load)
    elev_gain_m: Optional[float] = None
    hr_drift: Optional[float] = None      # within-run cardiac drift, HR-bearing runs/rides only
    structure: Optional[str] = None       # continuous | intervals (runs only)
    shape: Optional[str] = None           # e.g. "6x800m" (interval runs only)
    source: Optional[str] = None     # "recorded_laps" when the runner pressed the lap button on this past session (so a later report must NOT advise the lap button on it), else None; reuses the exact metrics.interval_structure.source vocabulary the subject-run rule keys on. Null-dropped, so packs stored before this change still validate on strict re-parse.
    long_run: Optional[bool] = None       # runner-relative long-run verdict, surfaced only when long
    pain: Optional[int] = None            # check-in pain score, surfaced only when present
    notes: Optional[str] = None           # bounded check-in notes, surfaced only when present


class RecentWeeksDayTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distance_km: float
    duration: str
    load: float


class RecentWeeksDay(BaseModel):
    """ADR 0026 Slice 2: one calendar day of a recent week — rest days INCLUDED (the
    coach reads the recovery structure, not only the sessions). `rest` marks a day with
    no activities (and it then carries no `day_totals`); an active day carries its
    `activities` list and the summed `day_totals`. Both `rest` and `day_totals` drop from
    serialization when they do not apply."""
    model_config = ConfigDict(extra="forbid")

    weekday: str                          # Mon .. Sun
    date: str                             # DD-MM-YY
    rest: Optional[bool] = None
    activities: List[RecentWeeksActivity] = []
    day_totals: Optional[RecentWeeksDayTotals] = None


class RecentWeeksTypeTotal(BaseModel):
    """One activity-type's contribution to a window's totals (the modality mix, so a
    walk is never counted as a run)."""
    model_config = ConfigDict(extra="forbid")

    type: str
    sessions: int
    distance_km: float
    duration: str
    load: float
    share_pct: float


class RecentWeeksAllTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: int
    distance_km: float
    duration: str
    load: float


class RecentWeeksTotals(BaseModel):
    """A window roll-up: the all-activities total plus a per-activity-TYPE breakdown."""
    model_config = ConfigDict(extra="forbid")

    all: RecentWeeksAllTotal
    by_type: List[RecentWeeksTypeTotal]


class RecentWeeksEndpoint(BaseModel):
    """A dated window bound (weekday + DD-MM-YY), so a window states its actual span and
    the coach never infers the range."""
    model_config = ConfigDict(extra="forbid")

    weekday: str
    date: str


class RecentWeeksVsTypicalMetric(BaseModel):
    """One metric's current-vs-typical read: the current value and the runner's OWN
    typical for a comparable window, plus a deadband `direction` and signed `pct`.
    `current`/`typical` are numbers for sessions/distance/load and an H:MM:SS string for
    duration. `typical`/`pct` drop (None) when the norm did not resolve."""
    model_config = ConfigDict(extra="forbid")

    current: Union[int, float, str]
    typical: Optional[Union[int, float, str]] = None
    direction: str                        # up | in_line | down | no_norm
    pct: Optional[float] = None


class RecentWeeksVsTypical(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: RecentWeeksVsTypicalMetric
    distance: RecentWeeksVsTypicalMetric
    duration: RecentWeeksVsTypicalMetric
    load: RecentWeeksVsTypicalMetric


class RecentWeeksRolling(BaseModel):
    """The trailing-7-days LOAD-VERDICT lane: a FULL 7 days ending on this run, so it
    compares directly to the runner's weekly norm with no pro-rating. Dated bounds +
    totals + the vs-your-typical verdict; it carries no day list (the calendar weeks own
    the day-resolved structure). This is the #653 fix — the firm verdict lives on the
    always-full window, not on a fudged partial calendar week."""
    model_config = ConfigDict(extra="forbid")

    start: RecentWeeksEndpoint
    end: RecentWeeksEndpoint
    label: str
    totals: RecentWeeksTotals
    vs_your_typical: Optional[RecentWeeksVsTypical] = None


class RecentWeeksCalendar(BaseModel):
    """One calendar week, day-resolved — the STRUCTURE lane. `this_week` is partial and
    carries NO vs-typical verdict (the #653 fix: no fudged partial-week verdict); the
    complete `last_week` carries `vs_your_typical`."""
    model_config = ConfigDict(extra="forbid")

    start: RecentWeeksEndpoint
    end: RecentWeeksEndpoint
    label: str
    complete: bool
    days_elapsed: int
    days: List[RecentWeeksDay]
    week_totals: RecentWeeksTotals
    vs_your_typical: Optional[RecentWeeksVsTypical] = None


class RecentWeeksContext(BaseModel):
    """ADR 0026 Slice 2 (#670): the single recent-training read that MERGES the retired
    `training_volume` + `recent_training` sections into one day-resolved picture on one
    week model. `rolling_7d` is the load-verdict lane (trailing 7 days vs the runner's
    weekly norm); `this_week`/`last_week` are the structure lane (day-resolved, rest days
    included). The day rows are the single source; the window totals and the rolling
    totals are sums over them, so nothing is duplicated. A deterministic FACT the coach
    may cite, but it never overrides the run's re-derived DerivedMetric or the safety
    floor. Emitted ONLY under a recent-weeks-aware prompt id, so the pack stays
    byte-stable otherwise. `has_baseline` is False when history is too thin for any
    vs-typical norm."""
    model_config = ConfigDict(extra="forbid")

    rolling_7d: RecentWeeksRolling
    this_week: RecentWeeksCalendar
    last_week: RecentWeeksCalendar
    has_baseline: bool


# --- ADR 0026: the five coaching-question groups ---------------------------
# Slice 1 (#672) reorganizes the pack from ~20 flat, milestone-named sections into
# five groups named for the coaching question they answer, plus the top-level
# safety floor. This slice is a PURE RELOCATION: each existing section moves under
# its group INTACT (no merges — intensity_read/recent_weeks are slices 3/2 — and no
# splits — longitudinal/calibration split in later slices). Each section keeps its
# exact Optionality, so the grouped shape carries the identical leaf facts.
#
# The grouped model is the CANONICAL shape (build_context_pack returns it; slices
# 2-5 add sections INTO these group models). Serialization derives the grouped dict
# from the existing flat fold pipeline (`to_grouped_dict` == `_nest(to_serializable_
# dict())`), so the flat pack stays byte-identical for prod (`coach_message_lean_v1`)
# and every historical prompt, and `flatten(grouped) == flat` holds by construction.


class ThisRun(BaseModel):
    """ADR 0026: what was this session, and how hard was it really?

    Slice 3 (#673) merges the this-run intensity lenses into `intensity_read` and
    promotes the safety `referral` to its own key, both under a new grouped prompt. The
    pre-slice-3 `perceived_effort`/`calibration`/`intensity` fields stay for every prior
    prompt (byte-stable); exactly one shape is populated per prompt (gated by
    PromptFeature) — under an intensity-read prompt `perceived_effort`/`calibration` are
    None (dropped) and `intensity_read`/`referral` appear, the combined `intensity`
    section drops with its feature, and `intensity.distribution` moves to
    `right_now.intensity_mix`."""
    model_config = ConfigDict(extra="forbid")

    activity: ActivityContext
    metrics: MetricsContext
    check_in: CheckInContext
    # Optional (#673): retired to None under an intensity-read prompt, populated on every
    # prior prompt (dropped from serialization when None via PACK_SECTIONS). Still declared
    # so a pre-slice-3 stored pack strict-parses.
    perceived_effort: Optional[PerceivedEffortContext] = None
    calibration: Optional[CalibrationContext] = None
    stream_view: Optional[Dict[str, Any]] = None
    block: Optional[BlockContext] = None
    intensity: Optional["IntensityContext"] = None
    # ADR 0026 Slice 3 (#673): the merged this-run intensity read + the promoted safety
    # nudge (a plain nudge string, not the machine `pattern`). Gated to the new grouped
    # prompt; None (dropped) elsewhere.
    intensity_read: Optional["IntensityRead"] = None
    referral: Optional[str] = None


class RightNow(BaseModel):
    """ADR 0026: how is the runner currently?

    Slice 2 (#670) redefines the CONTENT: `readiness` (renamed from `training_load`) and
    the merged day-resolved `recent_weeks` are the new shape, carried under a new grouped
    prompt. The pre-slice-2 `training_load`/`training_volume`/`recent_training` fields
    stay for every prior prompt (prod lean_v1, grouped_v1) so their pack is byte-stable;
    exactly one set is populated per prompt (gated by PromptFeature), the other is None
    and dropped."""
    model_config = ConfigDict(extra="forbid")

    training_load: Optional["TrainingLoadContext"] = None
    training_volume: Optional["TrainingVolumeContext"] = None
    recent_training: Optional["RecentTrainingContext"] = None
    # ADR 0026 Slice 2 (#670): the redefined content, gated to a new grouped prompt.
    readiness: Optional["ReadinessContext"] = None
    recent_weeks: Optional["RecentWeeksContext"] = None
    # ADR 0026 Slice 3 (#673): the recent intensity distribution + trend (the "how hard
    # lately" half of the retired intensity section), gated to the new grouped prompt.
    intensity_mix: Optional["IntensityMix"] = None


class TheRunner(BaseModel):
    """ADR 0026: who is this runner, and where are they going?"""
    model_config = ConfigDict(extra="forbid")

    profile: ProfileContext
    memory: Optional["MemoryContext"] = None
    training_history: Optional["TrainingHistoryContext"] = None


class OurThread(BaseModel):
    """ADR 0026: what did I say last time, and did it land? (slice 3 splits
    longitudinal.baseline_trend out to the_runner.typical_trend)."""
    model_config = ConfigDict(extra="forbid")

    adherence: AdherenceContext
    continuity: Optional[ContinuityContext] = Field(default_factory=ContinuityContext)
    longitudinal: Optional[LongitudinalContext] = None


class HowToCoach(BaseModel):
    """ADR 0026: delivery and philosophy, NEVER facts (voice lives in the system
    prompt, not the pack)."""
    model_config = ConfigDict(extra="forbid")

    corpus: Optional["CorpusContext"] = None
    stance: Optional["StanceContext"] = None


# The section -> group relocation map. Every flat section name maps to the group
# attribute it now lives under; a name absent here is a top-level meta field
# (salience, safety_rules, the retired stubs). This one table drives un-grouping
# (`_flat_data`), nesting (`_nest_flat`), and the legacy-flat loader (`load`).
_SECTION_GROUP: Dict[str, str] = {
    "activity": "this_run",
    "metrics": "this_run",
    "check_in": "this_run",
    "perceived_effort": "this_run",
    "calibration": "this_run",
    "stream_view": "this_run",
    "block": "this_run",
    "intensity": "this_run",
    "intensity_read": "this_run",
    "referral": "this_run",
    "training_load": "right_now",
    "training_volume": "right_now",
    "recent_training": "right_now",
    "readiness": "right_now",
    "recent_weeks": "right_now",
    "intensity_mix": "right_now",
    "profile": "the_runner",
    "memory": "the_runner",
    "training_history": "the_runner",
    "adherence": "our_thread",
    "continuity": "our_thread",
    "longitudinal": "our_thread",
    "corpus": "how_to_coach",
    "stance": "how_to_coach",
}

_GROUP_NAMES: tuple[str, ...] = (
    "this_run",
    "right_now",
    "the_runner",
    "our_thread",
    "how_to_coach",
)

# Sentinel so `from_sections` can tell an OMITTED salience/continuity (→ present empty
# object, the old default_factory behaviour) from an explicit None (→ the #522
# kill-switch drop). A plain `None` default could not distinguish the two.
_UNSET: Any = object()

# Flat section order preserved from the pre-ADR-0026 field declaration, so the flat
# serialized dict's key order is unchanged (tidy diffs; the hash uses sort_keys).
_FLAT_ORDER: tuple[str, ...] = (
    "activity",
    "metrics",
    "check_in",
    "profile",
    "recent_training_summary",
    "longitudinal",
    "perceived_effort",
    "adherence",
    "calibration",
    "believed_facts",
    "preference_profile",
    "narrative",
    "salience",
    "continuity",
    "block",
    "corpus",
    "stance",
    "training_load",
    "training_volume",
    "stream_view",
    "recent_training",
    "readiness",
    "recent_weeks",
    "training_history",
    "memory",
    "intensity",
    "intensity_read",
    "referral",
    "intensity_mix",
    "safety_rules",
)


def _nest_flat(flat: Dict[str, Any]) -> Dict[str, Any]:
    """Relocate a FLAT serialized pack dict into the five groups (ADR 0026).

    Pure key relocation: each section key present in `flat` moves under its group
    per `_SECTION_GROUP`; unmapped keys (salience/safety_rules/retired stubs) stay
    top-level. Empty groups are dropped, so `flatten(nest(flat)) == flat`. Used for
    both grouped serialization (`to_grouped_dict`) and the legacy-flat loader."""
    groups: Dict[str, Dict[str, Any]] = {g: {} for g in _GROUP_NAMES}
    out: Dict[str, Any] = {}
    for key, value in flat.items():
        group = _SECTION_GROUP.get(key)
        if group is not None:
            groups[group][key] = value
        else:
            out[key] = value
    for group in _GROUP_NAMES:
        if groups[group]:
            out[group] = groups[group]
    return out


def unnest_pack(data: Dict[str, Any]) -> Dict[str, Any]:
    """Hoist the ADR 0026 group sections back to the top level (inverse of `_nest_flat`).

    Tolerant, read-only, NON-validating: a flat dict (no group keys) is returned
    unchanged, a partial or empty dict is fine. For call sites that need to READ a
    section's presence from a stored pack of EITHER shape (e.g. the chat authority-
    tiering gate) without paying a full `load` + re-serialize (which would reject an
    empty `{}`)."""
    if not isinstance(data, dict):
        return data
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if key in _GROUP_NAMES and isinstance(value, dict):
            out.update(value)
        else:
            out[key] = value
    return out


class CoachContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ADR 0026: the five coaching-question groups + the top-level safety floor.
    this_run: ThisRun
    right_now: RightNow = Field(default_factory=RightNow)
    the_runner: TheRunner
    our_thread: OurThread
    how_to_coach: HowToCoach = Field(default_factory=HowToCoach)

    @model_validator(mode="before")
    @classmethod
    def _accept_flat_shape(cls, data: Any) -> Any:
        """ADR 0026: make `model_validate` tolerant of BOTH pack shapes. A grouped dict
        (or a kwargs dict from `from_sections`, which carries the group keys) validates
        as-is; a legacy FLAT dict — every historical stored pack, and any caller that
        still hands a flat dict — is relocated into the five groups via `_nest_flat`
        first. This is the ONE place the flat->grouped adaptation lives, so every
        `model_validate` call site (chat, eval harness/fixtures, tests) keeps working."""
        if isinstance(data, dict) and not any(g in data for g in _GROUP_NAMES):
            return _nest_flat(data)
        return data
    # A4 salience substrate (novelty + safety override): a routing signal for the
    # opener, not a coaching-question fact, so it stays top-level meta (ADR 0026 slice
    # 5 drops it from the fuller pack). Optional (#522) so COACH_SALIENCE_ENABLED can
    # drop it; default_factory keeps it a present empty object otherwise (byte-stable).
    salience: Optional[SalienceContext] = Field(default_factory=SalienceContext)
    safety_rules: SafetyRules
    # Retired-section stubs (kept top-level, NEVER populated, dropped when None) so a
    # historical stored FLAT pack carrying them still strict-parses under extra="forbid"
    # after `_nest_flat` relocates the live sections: #451 recent_training_summary and
    # the M4/ADR 0025 belief/preference/narrative loop. Only the retired-summary and
    # stub fields; the live sections all live under their groups now.
    recent_training_summary: Optional[RecentTrainingSummary] = None
    believed_facts: Optional[BelievedFactsContext] = None
    preference_profile: Optional[PreferenceProfile] = None
    narrative: Optional[NarrativeContext] = None

    # --- Flat accessor properties (ADR 0026) --------------------------------
    # Read-only flat views over the canonical grouped model, so the ~150 existing
    # `pack.<section>` typed reads (validator, eval, chat, tests) keep working without
    # churn. NEW code (slices 2-5) reads the grouped path directly; these accessors
    # are the compat layer, not the interface. The LLM-facing string paths (prompt +
    # validator evidence regexes) move to the grouped paths independently.
    @property
    def activity(self) -> ActivityContext:
        return self.this_run.activity

    @property
    def metrics(self) -> MetricsContext:
        return self.this_run.metrics

    @property
    def check_in(self) -> CheckInContext:
        return self.this_run.check_in

    @property
    def perceived_effort(self) -> PerceivedEffortContext:
        return self.this_run.perceived_effort

    @property
    def calibration(self) -> CalibrationContext:
        return self.this_run.calibration

    @property
    def stream_view(self) -> Optional[Dict[str, Any]]:
        return self.this_run.stream_view

    @property
    def block(self) -> Optional[BlockContext]:
        return self.this_run.block

    @property
    def intensity(self) -> Optional["IntensityContext"]:
        return self.this_run.intensity

    @property
    def intensity_read(self) -> Optional["IntensityRead"]:
        return self.this_run.intensity_read

    @property
    def referral(self) -> Optional[str]:
        return self.this_run.referral

    @property
    def intensity_mix(self) -> Optional["IntensityMix"]:
        return self.right_now.intensity_mix

    @property
    def training_load(self) -> Optional["TrainingLoadContext"]:
        return self.right_now.training_load

    @property
    def training_volume(self) -> Optional["TrainingVolumeContext"]:
        return self.right_now.training_volume

    @property
    def recent_training(self) -> Optional["RecentTrainingContext"]:
        return self.right_now.recent_training

    @property
    def readiness(self) -> Optional["ReadinessContext"]:
        return self.right_now.readiness

    @property
    def recent_weeks(self) -> Optional["RecentWeeksContext"]:
        return self.right_now.recent_weeks

    @property
    def profile(self) -> ProfileContext:
        return self.the_runner.profile

    @property
    def memory(self) -> Optional["MemoryContext"]:
        return self.the_runner.memory

    @property
    def training_history(self) -> Optional["TrainingHistoryContext"]:
        return self.the_runner.training_history

    @property
    def adherence(self) -> AdherenceContext:
        return self.our_thread.adherence

    @property
    def continuity(self) -> Optional[ContinuityContext]:
        return self.our_thread.continuity

    @property
    def longitudinal(self) -> Optional[LongitudinalContext]:
        return self.our_thread.longitudinal

    @property
    def corpus(self) -> Optional["CorpusContext"]:
        return self.how_to_coach.corpus

    @property
    def stance(self) -> Optional["StanceContext"]:
        return self.how_to_coach.stance

    @classmethod
    def from_sections(
        cls,
        *,
        activity: ActivityContext,
        metrics: MetricsContext,
        check_in: CheckInContext,
        profile: ProfileContext,
        adherence: AdherenceContext,
        safety_rules: SafetyRules,
        perceived_effort: Optional[PerceivedEffortContext] = None,
        calibration: Optional[CalibrationContext] = None,
        intensity_read: Optional["IntensityRead"] = None,
        referral: Optional[str] = None,
        intensity_mix: Optional["IntensityMix"] = None,
        longitudinal: Optional[LongitudinalContext] = None,
        salience: Optional[SalienceContext] = _UNSET,
        continuity: Optional[ContinuityContext] = _UNSET,
        block: Optional[BlockContext] = None,
        corpus: Optional["CorpusContext"] = None,
        stance: Optional["StanceContext"] = None,
        training_load: Optional["TrainingLoadContext"] = None,
        training_volume: Optional["TrainingVolumeContext"] = None,
        stream_view: Optional[Dict[str, Any]] = None,
        recent_training: Optional["RecentTrainingContext"] = None,
        readiness: Optional["ReadinessContext"] = None,
        recent_weeks: Optional["RecentWeeksContext"] = None,
        training_history: Optional["TrainingHistoryContext"] = None,
        memory: Optional["MemoryContext"] = None,
        intensity: Optional["IntensityContext"] = None,
        recent_training_summary: Optional[RecentTrainingSummary] = None,
        believed_facts: Optional[BelievedFactsContext] = None,
        preference_profile: Optional[PreferenceProfile] = None,
        narrative: Optional[NarrativeContext] = None,
    ) -> "CoachContextPack":
        """Build the grouped pack from the flat section objects the builder produces
        (ADR 0026). Slices 2-5 that add a section wire it here and on its group model.
        `salience`/`continuity` default to a present empty object (byte-stable) unless
        the caller explicitly passes None (the #522 kill-switch drop)."""
        return cls(
            this_run=ThisRun(
                activity=activity,
                metrics=metrics,
                check_in=check_in,
                perceived_effort=perceived_effort,
                calibration=calibration,
                stream_view=stream_view,
                block=block,
                intensity=intensity,
                intensity_read=intensity_read,
                referral=referral,
            ),
            right_now=RightNow(
                training_load=training_load,
                training_volume=training_volume,
                recent_training=recent_training,
                readiness=readiness,
                recent_weeks=recent_weeks,
                intensity_mix=intensity_mix,
            ),
            the_runner=TheRunner(
                profile=profile,
                memory=memory,
                training_history=training_history,
            ),
            our_thread=OurThread(
                adherence=adherence,
                continuity=(ContinuityContext() if continuity is _UNSET else continuity),
                longitudinal=longitudinal,
            ),
            how_to_coach=HowToCoach(corpus=corpus, stance=stance),
            salience=(SalienceContext() if salience is _UNSET else salience),
            safety_rules=safety_rules,
            recent_training_summary=recent_training_summary,
            believed_facts=believed_facts,
            preference_profile=preference_profile,
            narrative=narrative,
        )

    @classmethod
    def load(cls, data: Any) -> "CoachContextPack":
        """Parse a stored pack dict of EITHER shape (ADR 0026). A thin, self-documenting
        alias for `model_validate` (the `_accept_flat_shape` before-validator does the
        flat->grouped relocation), so the intent at the historical-pack load sites (chat,
        eval harness/fixtures) reads clearly."""
        return cls.model_validate(data)

    def _flat_data(self) -> Dict[str, Any]:
        """Un-group into the flat `{section: dict|None}` shape the pre-ADR-0026 model
        produced from `model_dump`, in the original field order. The flat fold
        pipeline in `to_serializable_dict` then runs UNCHANGED over it, so the flat
        serialized pack (prod + historical prompts) stays byte-identical."""
        groups = {
            "this_run": self.this_run,
            "right_now": self.right_now,
            "the_runner": self.the_runner,
            "our_thread": self.our_thread,
            "how_to_coach": self.how_to_coach,
        }
        data: Dict[str, Any] = {}
        for name in _FLAT_ORDER:
            group = _SECTION_GROUP.get(name)
            value = getattr(groups[group], name) if group is not None else getattr(self, name)
            data[name] = (
                value.model_dump(mode="python") if isinstance(value, BaseModel) else value
            )
        return data

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the FLAT JSON-primitive dict (prod + historical prompts).
        Byte-identical to the pre-ADR-0026 output. `to_grouped_dict` re-nests this."""
        data = self._flat_data()
        # The byte-stable-drop invariant lives in ONE place: every gated/optional
        # pack section that must vanish (not appear as a null key) when absent is a
        # PACK_SECTIONS descriptor, and this loop applies the drop uniformly. A
        # descriptor's optional `nested_drop` post-processor handles the sections
        # whose byte-stable trim is NOT a simple top-level pop (corpus's nested
        # `user_materials`, recent_training's deduplicated window/comparison fields,
        # training_volume's rolling_7d current-value fold). It receives the section
        # value AND the full pack dict, so a CROSS-section trim (training_volume only
        # folds out rolling_7d's current values when `recent_training` is present to
        # carry them) can see the other section. Adding a new gated section is ONE
        # descriptor here + the declared Optional field above; no new branch in this
        # method.
        for section in PACK_SECTIONS:
            value = data.get(section.field)
            if value is None:
                data.pop(section.field, None)
            elif section.nested_drop is not None:
                section.nested_drop(value, data)
        # #650: the "today" weekday anchor is always populated on a freshly built pack;
        # drop it only when None so a pack STORED before #650 (no weekday key) still
        # serialises byte-identically (the A2b legacy-dict round-trip invariant).
        act = data.get("activity")
        if isinstance(act, dict) and act.get("weekday") is None:
            act.pop("weekday", None)
        metrics = data.get("metrics")
        if isinstance(metrics, dict):
            # Collapse the gated interval/workout group to ONE signal when no session was
            # detected: the model needs a single "no workout" fact, not three null fields.
            # `interval_workout` is the collapsed field; it exists ONLY in this case. When a
            # session IS present, drop the (null) collapsed field and keep the structured
            # trio. Re-parse stays safe: all four fields default to None when absent.
            metrics.pop("interval_workout", None)
            wm = metrics.get("workout_match") or {}
            no_session = (
                metrics.get("interval_structure") is None
                and metrics.get("interval_kpis") is None
                and wm.get("match_score") is None
                and wm.get("detected_workout") is None
            )
            if no_session:
                metrics.pop("interval_structure", None)
                metrics.pop("workout_match", None)
                metrics.pop("interval_kpis", None)
                metrics["interval_workout"] = "none detected"
            # One-fact-one-place fold: discount_signals restates the run's HR drift,
            # which is already metrics.hr_drift (its sole home). Drop the copy.
            ds = metrics.get("discount_signals")
            if isinstance(ds, dict):
                ds.pop("hr_drift_pct", None)
            # Coach-facing trim (#637): the coach reads pace_s_per_km, not raw m/s.
            # Drop avg_speed_mps from each work_segment in the coach view; it stays
            # in the stored artifact (where _summarize's speed CV / consistency
            # still needs it). Serialization-only, so re-parse of a stored pack is
            # unaffected.
            istruct = metrics.get("interval_structure")
            if isinstance(istruct, dict):
                for seg in istruct.get("work_segments") or []:
                    if isinstance(seg, dict):
                        seg.pop("avg_speed_mps", None)
        # One-fact-one-place fold (the analytical sections restated a scalar that has
        # its own home elsewhere in the pack). Drop the copy at serialization; each
        # field stays declared so a stored pre-fold pack still re-parses. Sole homes:
        #   perceived_effort.rpe          -> check_in.rpe
        #   perceived_effort.effort_score -> metrics.effort_score
        #   calibration.hr_drift.observed_drift_pct -> metrics.hr_drift
        pe = data.get("perceived_effort")
        if isinstance(pe, dict):
            pe.pop("rpe", None)
            pe.pop("effort_score", None)
        cal = data.get("calibration")
        if isinstance(cal, dict) and isinstance(cal.get("hr_drift"), dict):
            cal["hr_drift"].pop("observed_drift_pct", None)
        return data

    def to_grouped_dict(self) -> Dict[str, Any]:
        """Serialise to the GROUPED JSON dict the ADR 0026 grouped prompt reads: the
        flat serialization re-nested into the five coaching-question groups. Derived
        from `to_serializable_dict`, so `flatten(to_grouped_dict()) == to_serializable_
        dict()` holds by construction and every flat-side fold/drop is inherited."""
        return _nest_flat(self.to_serializable_dict())

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key: the canonical hash of the serialized pack
        (sorted-key JSON with `default=str`), which the versioned-cache identity is
        defined against."""
        serialised = json.dumps(self.to_serializable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()


# --- The gated-pack-section registry (#493) ---------------------------------
# Adding a context-pack section that is GATED by prompt version and DROPPED from
# serialization to stay byte-stable used to require hand-edited branches kept in
# sync by discipline across context.py (build + call) and this file (the field +
# its drop branch). The byte-stable-drop rule — a null field changes the cache
# hash, so an absent section must NOT appear at all — was enforced in seven
# separate spots; a skewed branch broke the cache silently.
#
# `PackSection` makes the DROP wiring DATA: one descriptor per Optional section,
# and `to_serializable_dict` ITERATES them, so the byte-stable-drop invariant
# lives in ONE place. The drop fires whenever the field is None — the SAME
# behaviour for every existing section, unchanged.
#
# What stays declared, by necessity: Pydantic needs the Optional field declared
# STATICALLY on `CoachContextPack` (above), so the field itself is not generated.
# The descriptor's `field` names it, and `_assert_descriptors_match_fields` checks
# AT IMPORT that every descriptor names a real declared field — turning silent
# skew into a STARTUP failure.
#
# Reconciliation with the #492 `ReadTimeSignal` seam (the two compose, not fight):
# that seam owns "compute the section from a bounded history scan + abstain" and
# applies the PROMPT-FEATURE GATE at build time in `context.py`'s `gather`. This
# registry owns the orthogonal "DROP from serialization when absent (None), to
# stay byte-stable". A section can be both: its builder is a read-time signal
# (gated + built in context.py via `gather`) AND it is a `PackSection` here (so
# its None result is dropped). `gate_feature` below is the documentary cross-link
# to the prompt feature that the build-side gate keys on (None for an
# always-present-but-still-droppable field such as the retired
# `recent_training_summary`); the registry never re-applies the gate, it only
# drops, so the two seams stay independent and minimal.


@dataclass(frozen=True)
class PackSection:
    """One Optional pack section dropped from serialization when absent (None).

    ``field`` is the declared field name on ``CoachContextPack`` (checked to exist
    at import). ``gate_feature`` is the documentary cross-reference to the
    ``PromptFeature`` whose build-time gate (#492's ``gather`` / a ``context.py``
    build helper) decides whether the section is populated — ``None`` for a field
    that is droppable but not prompt-gated (the retired ``recent_training_summary``).
    ``nested_drop``, when set, is a post-processor applied to the NON-None value to
    perform a byte-stable trim that is not a simple top-level pop (corpus's nested
    ``user_materials`` key, recent_training's deduplicated window/comparison fields,
    training_volume's rolling_7d current-value fold). It is called as
    ``nested_drop(value, data)`` — ``data`` is the full serialized pack dict, so a
    CROSS-section trim can consult another section (training_volume only folds out
    its rolling_7d current values when ``recent_training`` is present to carry them).
    """

    field: str
    gate_feature: Optional[PromptFeature] = None
    nested_drop: Optional[Callable[[Any, Dict[str, Any]], None]] = None


def _drop_corpus_user_materials(corpus: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """P4 (#286): ``user_materials`` rides INSIDE the corpus section, but only under
    a user-materials-aware prompt. When None (every non-v7 corpus prompt) drop the
    nested key entirely, so the corpus section is byte-identical to its P1.2/P1.3
    shape under v4/v5/v6 (the activation boundary), exactly as the top-level
    Optional-and-drop idiom does for the section itself."""
    if corpus.get("user_materials") is None:
        corpus.pop("user_materials", None)


def _drop_training_volume_rolling_current(
    tv: Dict[str, Any], data: Dict[str, Any]
) -> None:
    """#400 fold (one lane per fact): drop `current_all`/`current_runs` from the
    `rolling_7d` window only. That window spans the same trailing 7 days as
    `recent_training.last_7d`, so its raw totals (current_all == that window's
    total_*/activity_count, current_runs == its by_type Run entry) were a second copy
    of the descriptive section. `rolling_7d` keeps only the vs-norm VERDICT
    (norm + direction + pct); the actual trailing-7d numbers live in `recent_training`.
    `calendar_week` is left untouched — it is the current Monday-to-date window, which
    no other section carries. Re-parse stays safe: both fields are Optional and default
    to None when absent.

    GATED on `recent_training` being present: the fold only holds when the descriptive
    lane exists to carry the numbers (recent-training-aware prompts, v11+). Under a
    volume-aware-but-not-recent-training prompt (v9/v10) `recent_training` is absent, so
    we KEEP rolling_7d's current values — dropping them there would lose the trailing-7d
    numbers entirely. (recent_training is dropped LATER in the PACK_SECTIONS loop, so its
    raw value is still readable here.)"""
    if not data.get("recent_training"):
        return
    rolling = tv.get("rolling_7d")
    if not rolling:
        return
    for comp in rolling.get("metrics", []):
        comp.pop("current_all", None)
        comp.pop("current_runs", None)


def _drop_recent_training_dedup(rt: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """#444/#451 pack trim: drop the deduplicated/empty fields from the
    recent-training section so they cost no tokens. Per window: the basis strings
    live once on the window (None on previous_30d). Per comparison: current_all/
    current_runs (already in the window totals/by_type and in training_volume), the
    per-row basis (moved up a level), and the 7d vs_typical_direction are all None
    and dropped. Re-parse stays safe — every dropped field is Optional and defaults
    to None when absent."""
    # #522: COACH_PREVIOUS_30D_ENABLED drops the whole previous_30d window (the
    # builder sets it None); pop the null key so the section stays byte-stable.
    if rt.get("previous_30d") is None:
        rt.pop("previous_30d", None)
    for wkey in ("last_7d", "last_30d", "previous_30d"):
        win = rt.get(wkey)
        if not win:
            continue
        for k in ("prev_basis", "typical_basis"):
            if win.get(k) is None:
                win.pop(k, None)
        for comp in win.get("comparisons", []):
            for k in [k for k, v in comp.items() if v is None]:
                comp.pop(k, None)
        # #650: drop the null session-shape fields so they cost no tokens — a continuous
        # run has no rep shape, and a non-run (ride/walk) has no running structure at all.
        # weekday is always populated (from the date), so it stays. Re-parse safe: both
        # default to None when absent.
        for act in win.get("activities", []):
            for k in ("structure", "interval_shape", "source", "long_run"):
                if act.get(k) is None:
                    act.pop(k, None)


def _strip_nulls_in_place(obj: Any) -> None:
    """Recursively remove None-valued keys from a nested dict/list structure."""
    if isinstance(obj, dict):
        for k in [k for k, v in obj.items() if v is None]:
            obj.pop(k)
        for v in obj.values():
            _strip_nulls_in_place(v)
    elif isinstance(obj, list):
        for v in obj:
            _strip_nulls_in_place(v)


def _drop_training_history_v2_fields(th: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 2 (#670): the grouped_v2-only training-history enrichments
    (per-bucket `by_type`/`avg_weekly_load`/`from_date`/`to_date` + the trait
    `peak_sustained_weekly_load`/`current_vs_peak_load_pct`) ride the SAME schema as the
    original 60d ladder. On the original ladder they are unset (None); pop exactly those
    keys so every prior prompt's section is BYTE-IDENTICAL to its pre-Slice-2 shape. The
    grouped_v2 ladder always populates them (a bucket's `by_type` is a non-empty list, the
    traits' `peak_sustained_weekly_load` is a set int), so this is a no-op there — and it
    deliberately does NOT touch the pre-existing Optional trait nulls (`current_vs_peak_pct`
    etc.), which still serialize as null exactly as today. Re-parse stays safe: every popped
    field defaults to None when absent."""
    traits = th.get("traits")
    if isinstance(traits, dict) and traits.get("peak_sustained_weekly_load") is None:
        traits.pop("peak_sustained_weekly_load", None)
        traits.pop("current_vs_peak_load_pct", None)
    for bucket in th.get("timeline", []) or []:
        if isinstance(bucket, dict) and bucket.get("by_type") is None:
            for key in ("from_date", "to_date", "avg_weekly_load", "by_type"):
                bucket.pop(key, None)


def _drop_recent_weeks_nulls(rw: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 2 (#670): make the recent_weeks section's sparse per-activity/day/
    verdict fields cost nothing when absent. Every leaf that does not apply (a no-HR
    session's `avg_hr`/`hr_drift`, a non-run's `structure`/`shape`, a run without a
    check-in's `rpe`/`pain`/`notes`, a rest day's `day_totals`, a `no_norm` metric's
    `typical`/`pct`) is Optional-None on the model; drop it recursively so the section
    carries only present facts. Re-parse stays safe — every dropped field defaults to
    None when absent."""
    _strip_nulls_in_place(rw)


def _drop_intensity_read_nulls(ir: Dict[str, Any], _data: Dict[str, Any]) -> None:
    """ADR 0026 Slice 3 (#673): make the intensity_read section's sparse fields cost
    nothing when absent. Drop the null leaves (a no-HR run's `band`, a no-zone run's
    `within_run`, a no-check-in run's `felt_vs_measured`, a no-drift run's
    `drift_vs_typical`, a clean run's `drift_vs_typical.confounded`) recursively, and
    drop the `confounders` list when empty so it only appears — leading the block — when
    a confounder actually fired. Re-parse stays safe: every dropped field defaults to
    None/empty when absent."""
    if ir.get("confounders") == []:
        ir.pop("confounders", None)
    _strip_nulls_in_place(ir)


# One descriptor per droppable Optional section. Order matches the historical
# branch order in to_serializable_dict so the serialized dict's key order — and
# therefore (post sort_keys, but kept tidy for diffs) the output — is unchanged.
PACK_SECTIONS: tuple[PackSection, ...] = (
    # The seven prompt-version-gated sections (#493 scope). Each is built + gated in
    # context.py (block/corpus/stance via build helpers, training_load/training_volume/
    # recent_training via the #492 ReadTimeSignal seam, stream_view via the deep flag),
    # and dropped here when None to stay byte-stable.
    PackSection("block"),  # A1 (AC8): block-of-one emits nothing.
    PackSection(
        "corpus", PromptFeature.CORPUS, nested_drop=_drop_corpus_user_materials
    ),  # AC1
    PackSection("stance", PromptFeature.STANCE),  # AC1
    PackSection("training_load", PromptFeature.TRAINING_LOAD),  # AC1
    PackSection(
        "training_volume",
        PromptFeature.VOLUME,
        nested_drop=_drop_training_volume_rolling_current,
    ),  # #400 (+ rolling_7d current-value fold)
    PackSection("stream_view", PromptFeature.STREAM_VIEW),  # #443
    PackSection(
        "recent_training",
        PromptFeature.RECENT_TRAINING,
        nested_drop=_drop_recent_training_dedup,
    ),  # #444
    # ADR 0026 Slice 2 (#670): the redefined right_now content, gated to the new grouped
    # prompt. readiness is a keyed rename (drops like training_load); recent_weeks is the
    # merged day-resolved read (its sparse per-activity/day/verdict nulls are stripped).
    PackSection("readiness", PromptFeature.READINESS),
    PackSection(
        "recent_weeks",
        PromptFeature.RECENT_WEEKS,
        nested_drop=_drop_recent_weeks_nulls,
    ),
    # #561, redefined ADR 0026 Slice 2 (#670): populated by EITHER the original
    # TRAINING_HISTORY signal (60d ladder, every prior prompt) or the TRAINING_HISTORY_2WK
    # signal (14d-bounded, enriched ladder, grouped_v2) — mutually exclusive, so this ONE
    # descriptor drops the field only when both abstain. The nested_drop surgically strips
    # the grouped_v2-only enrichment keys on the original ladder, keeping it byte-identical.
    PackSection(
        "training_history",
        PromptFeature.TRAINING_HISTORY,
        nested_drop=_drop_training_history_v2_fields,
    ),
    PackSection("memory", PromptFeature.MEMORY),  # ADR 0025 runner memory profile
    PackSection("intensity", PromptFeature.INTENSITY),  # #578 intensity distribution + trend
    # ADR 0026 Slice 3 (#673): the merged this-run intensity read + the promoted safety
    # referral + the recent intensity mix, gated to the new grouped prompt. perceived_effort
    # and calibration (always-present under every prior prompt) are RETIRED to None under an
    # intensity-read prompt; their descriptors below drop them then, byte-stable elsewhere
    # (non-None under every prior prompt, so the drop never fires there).
    PackSection(
        "intensity_read",
        PromptFeature.INTENSITY_READ,
        nested_drop=_drop_intensity_read_nulls,
    ),
    PackSection("referral", PromptFeature.INTENSITY_READ),
    PackSection("intensity_mix", PromptFeature.INTENSITY_MIX),
    PackSection("perceived_effort"),  # retired to None under an intensity-read prompt
    PackSection("calibration"),       # retired to None under an intensity-read prompt
    # #451: the retired legacy summary — droppable but NOT prompt-gated (no longer
    # populated; a pre-#451 stored pack still round-trips its real object unchanged).
    PackSection("recent_training_summary"),
    # M4 (ADR 0025): the retired belief / preference / narrative stubs — never
    # populated, dropped when None, kept only so a pre-M4 stored pack still parses.
    PackSection("believed_facts"),
    PackSection("preference_profile"),
    PackSection("narrative"),
    # #522 coach-input kill switches. Normally always-present sections that the
    # COACH_*_ENABLED settings can drop (the context.py builder returns None when the
    # flag is off). Not prompt-feature-gated — the gate is a runtime setting, applied
    # in context.py; here they only DROP when None, like every other descriptor.
    PackSection("longitudinal"),  # COACH_LONGITUDINAL_ENABLED
    PackSection("salience"),      # COACH_SALIENCE_ENABLED
    PackSection("continuity"),    # COACH_CONTINUITY_ENABLED
)


def _assert_descriptors_match_fields() -> None:
    """Fail loudly AT IMPORT on a descriptor/field mismatch.

    Every ``PackSection.field`` MUST name a FLAT section that the drop loop can pop
    from the un-grouped ``_flat_data`` dict — i.e. a section relocated into a group
    (``_SECTION_GROUP``) or a top-level meta field on ``CoachContextPack`` (ADR 0026;
    the group container fields ``this_run``/… are not flat sections). A typo or a
    renamed field turns from a silent byte-stability break into a startup
    ``RuntimeError``, pairing with the #328 prompt-feature manifest."""
    flat_universe = set(_SECTION_GROUP) | (
        set(CoachContextPack.model_fields) - set(_GROUP_NAMES)
    )
    seen: set[str] = set()
    for section in PACK_SECTIONS:
        if section.field in seen:
            raise RuntimeError(
                f"PackSection registry: duplicate descriptor for field "
                f"{section.field!r}."
            )
        seen.add(section.field)
        if section.field not in flat_universe:
            raise RuntimeError(
                f"PackSection registry: descriptor names field {section.field!r}, "
                f"which is not a flat section (grouped section or top-level meta). "
                f"Fix the descriptor or declare the field."
            )


_assert_descriptors_match_fields()
