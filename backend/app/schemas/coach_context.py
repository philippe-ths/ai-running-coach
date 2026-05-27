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

    activity_class: Optional[str]
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


class CoachContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity: ActivityContext
    metrics: MetricsContext
    check_in: CheckInContext
    profile: ProfileContext
    recent_training_summary: RecentTrainingSummary
    safety_rules: SafetyRules

    def to_serializable_dict(self) -> Dict[str, Any]:
        """Serialise to the JSON-primitive dict shape the LLM input and DB column expect."""
        return self.model_dump(mode="python")

    def fingerprint(self) -> str:
        """Deterministic SHA-256 cache key. Byte-identical to the legacy hash_context_pack output."""
        serialised = json.dumps(self.to_serializable_dict(), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()
