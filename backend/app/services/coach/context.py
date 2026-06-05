"""
Context pack builder — assembles all facts the LLM needs into a typed pack.

No computation happens here. This module only gathers and shapes existing data
from the database (activity, metrics, check-in, profile, trends).
"""

import hashlib
import json
from datetime import timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import Activity, RunnerBaseline, UserProfile
from app.models.coach_report import CoachReport
from app.services.analysis.baseline import bucket_key
from app.services.analysis.classifier import Classification, compose_headline  # noqa: F401
from app.schemas.coach_context import (
    ActivityContext,
    BaselineTrendDelta,
    CheckInContext,
    CoachContextPack,
    LongitudinalContext,
    MetricsContext,
    PriorReportDigest,
    ProfileContext,
    RecentTrainingSummary,
    SafetyRules,
    TrainingPeriodSummary,
)
from app.services.trends import _query_activity_facts
from app.services.units.cadence import normalize_cadence_spm

# Bound on how many prior reports the longitudinal digest carries into the pack.
# The brief asks for "the last 1-2 reports"; two is enough to advance the
# narrative while keeping the pack from growing with history (M4 token bound).
_MAX_PRIOR_REPORTS = 2

# Defensive cap on rows scanned to find those reports. Reports are version-keyed
# (a few rows per activity at most), so 50 rows comfortably covers the two most
# recent distinct activities while keeping the read bounded as history grows.
_PRIOR_REPORT_SCAN_LIMIT = 50


def build_context_pack(db: Session, activity: Activity) -> CoachContextPack:
    """Assemble all facts the LLM needs. No computation, just data gathering."""
    metrics = activity.metrics
    check_in = getattr(activity, "check_in", None)
    profile: Optional[UserProfile] = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == activity.user_id)
        .first()
    )

    # Zone calibration: only true if user explicitly set max_hr with a known source
    has_explicit_max_hr = bool(
        profile
        and profile.max_hr
        and profile.max_hr > 100
        and getattr(profile, "max_hr_source", None)  # must have a source
    )
    zones_calibrated = has_explicit_max_hr
    if has_explicit_max_hr:
        zones_basis = f"user_{profile.max_hr_source}"
    else:
        zones_basis = "uncalibrated"

    # Recent training summary relative to this activity's date
    activity_date = activity.start_date.date()

    facts_7d = _query_activity_facts(
        db, activity_date - timedelta(days=7), activity_date
    )
    facts_28d = _query_activity_facts(
        db, activity_date - timedelta(days=28), activity_date
    )
    facts_prev_28d = _query_activity_facts(
        db, activity_date - timedelta(days=56), activity_date - timedelta(days=28)
    )

    def _summarize(facts) -> TrainingPeriodSummary:
        return TrainingPeriodSummary(
            activity_count=len(facts),
            total_distance_m=sum(f.distance_m for f in facts),
            total_moving_time_s=sum(f.moving_time_s for f in facts),
            total_effort=round(sum(f.effort_score or 0 for f in facts), 1),
        )

    # Training context: intensity distribution and recency signals (persisted
    # by the analysis pipeline on DerivedMetric).
    training_context = metrics.training_context if metrics else None

    return CoachContextPack(
        activity=ActivityContext(
            date=activity.start_date.isoformat(),
            name=activity.name,
            type=activity.user_intent or activity.type,
            distance_m=activity.distance_m,
            moving_time_s=activity.moving_time_s,
            avg_hr=activity.avg_hr,
            max_hr=activity.max_hr,
            avg_cadence=normalize_cadence_spm(
                activity.user_intent or activity.type, activity.avg_cadence
            ),
            elev_gain_m=activity.elev_gain_m,
        ),
        metrics=MetricsContext(
            headline=compose_headline(activity, Classification.from_metrics(metrics)),
            effort=metrics.effort if metrics else None,
            duration_class=metrics.duration_class if metrics else None,
            structure=metrics.structure if metrics else None,
            is_hilly=metrics.is_hilly if metrics else None,
            is_race=metrics.is_race if metrics else None,
            effort_score=round(metrics.effort_score, 1) if metrics else None,
            hr_drift=round(metrics.hr_drift, 1) if metrics and metrics.hr_drift else None,
            pace_variability=(
                round(metrics.pace_variability, 1)
                if metrics and metrics.pace_variability
                else None
            ),
            flags=metrics.flags if metrics else [],
            confidence=metrics.confidence if metrics else "low",
            confidence_reasons=metrics.confidence_reasons if metrics else [],
            time_in_zones=metrics.time_in_zones if metrics else None,
            zones_calibrated=zones_calibrated,
            zones_basis=zones_basis,
            efficiency_analysis=metrics.efficiency_analysis if metrics else None,
            stops_analysis=metrics.stops_analysis if metrics else None,
            interval_structure=metrics.interval_structure if metrics else None,
            workout_match=metrics.workout_match if metrics else None,
            interval_kpis=metrics.interval_kpis if metrics else None,
            risk_level=metrics.risk_level if metrics else None,
            risk_score=metrics.risk_score if metrics else None,
            risk_reasons=metrics.risk_reasons if metrics else [],
            training_context=training_context,
            discount_signals=metrics.discount_signals if metrics else None,
        ),
        check_in=CheckInContext(
            rpe=check_in.rpe if check_in else None,
            pain_score=check_in.pain_score if check_in else None,
            pain_location=check_in.pain_location if check_in else None,
            sleep_quality=check_in.sleep_quality if check_in else None,
            notes=check_in.notes if check_in else None,
        ),
        profile=ProfileContext(
            goal_type=profile.goal_type if profile else None,
            experience_level=profile.experience_level if profile else None,
            weekly_days_available=profile.weekly_days_available if profile else None,
            injury_notes=profile.injury_notes if profile else None,
            max_hr=profile.max_hr if profile else None,
            max_hr_source=getattr(profile, "max_hr_source", None) if profile else None,
            current_weekly_km=profile.current_weekly_km if profile else None,
        ),
        recent_training_summary=RecentTrainingSummary(
            last_7d=_summarize(facts_7d),
            last_28d=_summarize(facts_28d),
            previous_28d=_summarize(facts_prev_28d),
        ),
        longitudinal=_build_longitudinal_context(db, activity),
        safety_rules=SafetyRules(
            never_diagnose=True,
            pain_severe_threshold=7,
            no_invented_facts=True,
        ),
    )


def _build_longitudinal_context(db: Session, activity: Activity) -> LongitudinalContext:
    """Assemble the M4 longitudinal contrast: a digest of the runner's last 1-2
    reports plus the M2 baseline trend matching this activity's context bucket.

    Reads existing rows only; computes nothing new. Both halves degrade to
    empty/None (no prior reports, no comparable trend) without failing.
    """
    return LongitudinalContext(
        prior_reports=_prior_report_digests(db, activity),
        baseline_trend=_matching_baseline_trend(db, activity),
    )


def _prior_report_digests(db: Session, activity: Activity) -> list[PriorReportDigest]:
    """Digest of the runner's most recent non-fallback reports before this run.

    Returns at most `_MAX_PRIOR_REPORTS` entries, most recent first, one per
    activity (the latest report when an activity has several versioned rows).
    Only the lead_argument and next-step actions are carried, never the full
    report body — that is the M4 token bound.
    """
    rows = (
        db.query(CoachReport, Activity.start_date)
        .join(Activity, CoachReport.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.id != activity.id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date < activity.start_date,
            CoachReport.is_fallback == False,  # noqa: E712
        )
        # created_at picks the latest version per activity; id is a stable
        # final tiebreaker so a same-instant tie is deterministic.
        .order_by(
            Activity.start_date.desc(),
            CoachReport.created_at.desc(),
            CoachReport.id.desc(),
        )
        .limit(_PRIOR_REPORT_SCAN_LIMIT)
        .all()
    )

    digests: list[PriorReportDigest] = []
    seen: set = set()
    for report, start_date in rows:
        if report.activity_id in seen:
            continue  # keep only the latest report per prior activity
        seen.add(report.activity_id)
        digests.append(_digest_from_report(report.report or {}, start_date))
        if len(digests) >= _MAX_PRIOR_REPORTS:
            break
    return digests


def _digest_from_report(report: Dict[str, Any], start_date) -> PriorReportDigest:
    """Project a stored report dict down to its longitudinal digest fields."""
    lead = report.get("lead_argument")
    if isinstance(lead, dict):
        lead_text = lead.get("text")
    elif isinstance(lead, str):
        lead_text = lead
    else:
        lead_text = None

    next_steps: list[str] = []
    for step in (report.get("next_steps") or []):
        if not isinstance(step, dict):
            continue
        # Coerce to str defensively: the write path validates these as strings,
        # but a hand-edited or future-shape row must not raise here.
        action = str(step.get("action") or "").strip()
        details = str(step.get("details") or "").strip()
        if action and details:
            next_steps.append(f"{action} ({details})")
        elif action:
            next_steps.append(action)

    return PriorReportDigest(
        activity_date=start_date.isoformat() if start_date else "",
        headline=report.get("headline"),
        lead_argument=lead_text,
        next_steps=next_steps,
    )


def _matching_baseline_trend(
    db: Session, activity: Activity
) -> Optional[BaselineTrendDelta]:
    """The RunnerBaseline trend for this activity's context bucket, if any.

    Returns None when there is no baseline, no metrics, no matching bucket, or
    the matching bucket is still abstaining (too few comparable samples).
    """
    metrics = activity.metrics
    if metrics is None:
        return None

    baseline: Optional[RunnerBaseline] = (
        db.query(RunnerBaseline)
        .filter(RunnerBaseline.user_id == activity.user_id)
        .first()
    )
    if baseline is None or not baseline.bucketed_trends:
        return None

    average_temp = None
    if activity.raw_summary:
        average_temp = activity.raw_summary.get("average_temp")

    key = bucket_key(metrics.effort, metrics.is_hilly, average_temp)
    bucket = baseline.bucketed_trends.get(key)
    if not bucket or bucket.get("abstained"):
        return None

    return BaselineTrendDelta(
        bucket=key,
        sample_count=bucket.get("sample_count", 0),
        efficiency_factor=bucket.get("efficiency_factor"),
        hr_drift=bucket.get("hr_drift"),
    )


def hash_context_pack(pack: Dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of a legacy dict-shaped pack.

    Retained for migration safety (tests pin the typed model's fingerprint against this);
    new code should call CoachContextPack.fingerprint() instead.
    """
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, default=str).encode()
    ).hexdigest()
