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

from pydantic import BaseModel, ConfigDict


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
    safety_rules: SafetyRules

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the JSON-primitive dict shape the LLM input and DB column expect."""
        return self.model_dump(mode="python")

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key. Byte-identical to the legacy hash_context_pack output."""
        serialised = json.dumps(self.to_serializable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()
