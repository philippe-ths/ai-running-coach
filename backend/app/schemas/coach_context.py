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

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.material import DistilledMaterial
from app.services.coach.prompt_features import PromptFeature


class ActivityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
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
    showed); `recommended_weighting` is "rpe_over_hr" when an HR confounder fired
    (RPE survives HR distortion), else "balanced", or "hr_only" with no RPE.
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


class TrainingHistoryBucket(BaseModel):
    """#561: one coarse, far-horizon volume bucket in the level-of-detail ladder.

    The deep history reaches the coach at DECAYING resolution: `recent_training`
    owns the detailed last ~60 days in full (its last_7d/last_30d/previous_30d
    windows all live within 0-60d), so this ladder starts AFTER that and widens as it
    goes back (2-6 months, 6-12 months, 1-2 years, 2-5 years, 5+ years), each bucket
    reporting an AVERAGE WEEKLY rate so buckets of unequal width stay directly
    comparable (the runner's own "20 km/week" read). The weekly average divides by
    the weeks the bucket actually spans WITHIN the runner's history (so a
    partially-covered bucket is not deflated), mirroring the volume.py clamp. A
    bucket is emitted only when it holds real data, so the ladder self-sizes to how
    far the history reaches."""
    model_config = ConfigDict(extra="forbid")

    label: str            # human horizon, e.g. "2-6 months ago"
    start_days_ago: int   # inclusive age lower bound
    end_days_ago: int     # exclusive age upper bound (== start of the open tail's coverage end)
    weeks: float          # weeks spanned within history (the averaging denominator)
    avg_weekly_distance_m: int
    avg_weekly_sessions: float
    run_share_pct: float  # share of this bucket's sessions that are runs (modality mix)


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


class CoachContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity: ActivityContext
    metrics: MetricsContext
    check_in: CheckInContext
    profile: ProfileContext
    # #451: the legacy coarse recent-volume summary, RETIRED. No longer populated —
    # the rich `recent_training` section plus the `training_volume` vs-norm verdict
    # supersede it (the three-way overlap is gone). Kept as an Optional field so
    # STORED pre-#451 packs (which carry it) still validate under extra="forbid" — the
    # chat read path and eval harness both strict-parse historical packs — and dropped
    # from serialization when None so new packs never carry it.
    recent_training_summary: Optional[RecentTrainingSummary] = None
    # M4 longitudinal contrast. Normally always present; Optional so the #522
    # COACH_LONGITUDINAL_ENABLED kill switch can drop the whole section (the builder
    # returns None when disabled, the PACK_SECTIONS registry pops it). Every normal
    # construction still passes the real object, so the default path is byte-stable.
    longitudinal: Optional[LongitudinalContext] = None
    perceived_effort: PerceivedEffortContext
    adherence: AdherenceContext
    calibration: CalibrationContext
    # M4 (ADR 0025) retired the belief loop + A2c narrative + M10 preference.
    # These three fields are kept as never-populated Optional deprecated STUBS so a
    # historical stored pack carrying them still strict-parses under extra="forbid"
    # (the chat read path + eval harness load old packs); they are never set by the
    # builder and drop from serialization when None via the PACK_SECTIONS registry.
    believed_facts: Optional[BelievedFactsContext] = None
    preference_profile: Optional[PreferenceProfile] = None
    narrative: Optional[NarrativeContext] = None
    # A4 salience substrate: novelty + safety override. Defaulted (empty) so every
    # pre-A4 fixture and the legacy/single-shot paths stay valid; the opener and
    # fuller builders populate it. Adding it changes the pack fingerprint, so v2
    # reports regenerate — the same intentional shape-change A2c made for narrative.
    # Optional (#522) so COACH_SALIENCE_ENABLED can drop the section; the
    # default_factory keeps it an empty object (present, byte-stable) for every
    # construction that does not explicitly pass None.
    salience: Optional[SalienceContext] = Field(default_factory=SalienceContext)
    # A4 fuller-turn continuity (opener prose + any reply). Defaulted (empty) for
    # the opener stage and single-shot exchanges; only the fuller path populates it.
    # Optional (#522) so COACH_CONTINUITY_ENABLED can drop the section; the
    # default_factory keeps it an empty object (present, byte-stable) otherwise.
    continuity: Optional[ContinuityContext] = Field(default_factory=ContinuityContext)
    # A1 multi-member block aggregate. None for a block-of-one and OMITTED from
    # serialization entirely (AC8: the solo-run pack stays byte-stable pre/post A1).
    block: Optional[BlockContext] = None
    # P1.2 coaching corpus (ADR 0014). None under every non-corpus prompt and OMITTED
    # from serialization entirely, exactly like `block`, so the pack stays byte-stable
    # pre/post P1.2 (AC1/AC7); populated only under a corpus-aware prompt id.
    corpus: Optional["CorpusContext"] = None
    # P1.3 coaching stance — emphasis axes (ADR 0015). None under every non-stance
    # prompt and OMITTED from serialization entirely, exactly like `corpus`/`block`,
    # so the pack stays byte-stable pre/post P1.3 (AC1/AC7); populated only under a
    # stance-aware prompt id.
    stance: Optional["StanceContext"] = None
    # P3 training-load readiness (ADR 0016). None under every non-training-load
    # prompt and OMITTED from serialization entirely, exactly like `corpus`/`stance`/
    # `block`, so the pack stays byte-stable pre/post P3 (AC1/AC7); populated only
    # under a training-load-aware prompt id.
    training_load: Optional["TrainingLoadContext"] = None
    # #400 frequency-/volume-vs-norm. None under every non-volume prompt and OMITTED
    # from serialization entirely, exactly like `training_load`/`corpus`/`block`, so
    # the pack stays byte-stable pre/post #400; populated only under a volume-aware
    # prompt id.
    training_volume: Optional["TrainingVolumeContext"] = None
    # #443 consolidated stream view (A2a): the <=60-pt aligned HR/pace/grade/cadence
    # downsample, carried in the DEFAULT pack so the coach reads the run's shape on
    # every report. None under every non-stream-view prompt and OMITTED from
    # serialization entirely, exactly like `training_volume`/`training_load`/`corpus`/
    # `block`, so the pack stays byte-stable pre/post #443; populated only under a
    # stream-view-aware prompt id. It is a downsampled VIEW, never a measurement, and
    # never overrides the re-derived metrics (prompt addendum).
    stream_view: Optional[Dict[str, Any]] = None
    # #444 modality-aware recent-training picture (per-type breakdown + per-activity
    # detail + vs-typical/vs-prev with basis). None under every non-recent-training
    # prompt and OMITTED from serialization entirely, exactly like the other gated
    # sections, so the pack stays byte-stable pre/post #444; populated only under a
    # recent-training-aware prompt id.
    recent_training: Optional["RecentTrainingContext"] = None
    # #561 multi-year training-history picture (the LOD volume ladder + durability
    # traits). None under every non-training-history prompt and OMITTED from
    # serialization entirely, exactly like the other gated sections, so the pack
    # stays byte-stable pre/post #561; populated only under a training-history-aware
    # prompt id, and only when history beyond the recent window exists to describe.
    training_history: Optional["TrainingHistoryContext"] = None
    # ADR 0025 runner memory profile, surfaced whole. None under every non-memory
    # prompt and OMITTED from serialization (the gated-section idiom), so the pack
    # stays byte-stable pre/post v13; populated only under a memory-aware prompt id,
    # and only when a profile row exists for the runner.
    memory: Optional["MemoryContext"] = None
    # #578 intensity-distribution-and-trend signal. None under every non-intensity prompt
    # and OMITTED from serialization (the gated-section idiom), so the pack stays
    # byte-stable pre/post v14; populated only under an intensity-aware prompt id, and
    # only when there is something to say (the builder returns None otherwise).
    intensity: Optional["IntensityContext"] = None
    safety_rules: SafetyRules

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the JSON-primitive dict shape the LLM input and DB column expect."""
        data = self.model_dump(mode="python")
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

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key. Byte-identical to the legacy hash_context_pack output."""
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
    PackSection("training_history", PromptFeature.TRAINING_HISTORY),  # #561
    PackSection("memory", PromptFeature.MEMORY),  # ADR 0025 runner memory profile
    PackSection("intensity", PromptFeature.INTENSITY),  # #578 intensity distribution + trend
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

    Every ``PackSection.field`` MUST name a declared Optional field on
    ``CoachContextPack`` whose default is ``None`` (so the drop-when-None contract
    holds). A typo or a renamed field turns from a silent byte-stability break into
    a startup ``RuntimeError``. This is the import-time guard the #493 constraint
    requires, pairing with the #328 prompt-feature manifest: the field stays
    statically declared, the registry asserts it exists."""
    model_fields = CoachContextPack.model_fields
    seen: set[str] = set()
    for section in PACK_SECTIONS:
        if section.field in seen:
            raise RuntimeError(
                f"PackSection registry: duplicate descriptor for field "
                f"{section.field!r}."
            )
        seen.add(section.field)
        if section.field not in model_fields:
            raise RuntimeError(
                f"PackSection registry: descriptor names field {section.field!r}, "
                f"which is not declared on CoachContextPack. Declare the Optional "
                f"field on the model or fix the descriptor."
            )


_assert_descriptors_match_fields()
