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


class CoachContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity: ActivityContext
    metrics: MetricsContext
    check_in: CheckInContext
    profile: ProfileContext
    recent_training_summary: RecentTrainingSummary
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
    safety_rules: SafetyRules

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the JSON-primitive dict shape the LLM input and DB column expect."""
        return self.model_dump(mode="python")

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key. Byte-identical to the legacy hash_context_pack output."""
        serialised = json.dumps(self.to_serializable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()
