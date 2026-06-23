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
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.material import DistilledMaterial


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
    interval_structure: Optional[Dict[str, Any]]
    workout_match: Optional[Dict[str, Any]]
    interval_kpis: Optional[Dict[str, Any]]
    risk_level: Optional[str]
    risk_score: Optional[int]
    risk_reasons: Optional[List[str]]
    training_context: Optional[Dict[str, Any]]
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
    no CheckIn exists. `effort_score` is the raw TRIMP-like load, carried for
    context only — divergence is computed against the `effort_axis` intensity.
    """
    model_config = ConfigDict(extra="forbid")

    rpe: Optional[int]
    effort_axis: Optional[str]
    effort_score: Optional[float]
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
    deterministic FACT; it never overrides this run's re-derived DerivedMetric."""
    model_config = ConfigDict(extra="forbid")

    metric: str  # sessions | distance_m | moving_time_s | effort_score
    current_all: Union[int, float]
    current_runs: Union[int, float]
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
    current_all: Union[int, float]
    current_runs: Union[int, float]
    vs_typical_pct: Optional[float] = None
    vs_typical_direction: str  # up | in_line | down | no_norm
    vs_prev_pct: Optional[float] = None
    vs_prev_direction: str     # up | in_line | down | no_norm
    # #451: None on a window that carries no vs-typical read (the 7d window — its
    # weekly vs-norm verdict lives in `training_volume`, so recent_training does not
    # duplicate it). Present (a self-describing string) wherever vs_typical is computed.
    typical_basis: Optional[str] = None
    prev_basis: str


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


class RecentTrainingContext(BaseModel):
    """#444: the modality-aware recent-training picture — the rich successor to
    `recent_training_summary` (per-type breakdown + per-activity detail + vs-typical/
    vs-prev with self-describing basis). Emitted ONLY under a recent-training-aware
    prompt id, so the pack stays byte-stable otherwise. `has_baseline` is False when
    history is too thin for any vs-typical norm (every direction then `no_norm`)."""
    model_config = ConfigDict(extra="forbid")

    last_7d: RecentTrainingWindow
    last_30d: RecentTrainingWindow
    previous_30d: RecentTrainingWindow
    has_baseline: bool


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
    longitudinal: LongitudinalContext
    perceived_effort: PerceivedEffortContext
    adherence: AdherenceContext
    believed_facts: BelievedFactsContext
    calibration: CalibrationContext
    preference_profile: PreferenceProfile
    # A2c durable-memory narrative (voice only; never overrides today's data).
    # Defaulted so the many call sites that build a pack without a narrative (the
    # first exchange, every test fixture) stay valid; build_b_baseline populates
    # it from the stored CoachNarrative row when one exists.
    narrative: NarrativeContext = NarrativeContext()
    # A4 salience substrate: novelty + safety override. Defaulted (empty) so every
    # pre-A4 fixture and the legacy/single-shot paths stay valid; the opener and
    # fuller builders populate it. Adding it changes the pack fingerprint, so v2
    # reports regenerate — the same intentional shape-change A2c made for narrative.
    salience: SalienceContext = Field(default_factory=SalienceContext)
    # A4 fuller-turn continuity (opener prose + any reply). Defaulted (empty) for
    # the opener stage and single-shot exchanges; only the fuller path populates it.
    continuity: ContinuityContext = Field(default_factory=ContinuityContext)
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
    safety_rules: SafetyRules

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the JSON-primitive dict shape the LLM input and DB column expect."""
        data = self.model_dump(mode="python")
        if data.get("block") is None:
            # AC8: a block-of-one emits nothing new — not even a null key.
            data.pop("block", None)
        if data.get("corpus") is None:
            # AC1: a non-corpus prompt emits nothing — not even a null key.
            data.pop("corpus", None)
        else:
            # P4 (#286): user_materials rides INSIDE the corpus section, but only
            # under a user-materials-aware prompt. When None (every non-v7 corpus
            # prompt) drop the nested key entirely, so the corpus section is
            # byte-identical to its P1.2/P1.3 shape under v4/v5/v6 (the activation
            # boundary), exactly as the top-level Optional-and-drop idiom does.
            if data["corpus"].get("user_materials") is None:
                data["corpus"].pop("user_materials", None)
        if data.get("stance") is None:
            # AC1: a non-stance prompt emits nothing — not even a null key.
            data.pop("stance", None)
        if data.get("training_load") is None:
            # AC1: a non-training-load prompt emits nothing — not even a null key.
            data.pop("training_load", None)
        if data.get("training_volume") is None:
            # #400: a non-volume prompt emits nothing — not even a null key.
            data.pop("training_volume", None)
        if data.get("stream_view") is None:
            # #443: a non-stream-view prompt emits nothing — not even a null key, so
            # the pack stays byte-stable under v9 and below.
            data.pop("stream_view", None)
        if data.get("recent_training") is None:
            # #444: a non-recent-training prompt emits nothing — not even a null key.
            data.pop("recent_training", None)
        if data.get("recent_training_summary") is None:
            # #451: the legacy summary is retired and no longer populated; drop the
            # null key so new packs omit it. A pre-#451 stored pack still carries the
            # real object (non-None), so it round-trips unchanged.
            data.pop("recent_training_summary", None)
        return data

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key. Byte-identical to the legacy hash_context_pack output."""
        serialised = json.dumps(self.to_serializable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()
