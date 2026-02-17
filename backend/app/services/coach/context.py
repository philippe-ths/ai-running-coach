"""
Context pack builder — assembles all facts the LLM needs into a single JSON dict.

No computation happens here. This module only gathers and shapes existing data
from the database (activity, metrics, check-in, profile, trends).
"""

import hashlib
import json
from datetime import timedelta
from typing import List, Optional

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.models import Activity, DerivedMetric, UserProfile
from app.services.trends import _query_activity_facts

# Activity classes considered "hard" for training context
HARD_CLASSES = {"Intervals", "Tempo", "Race", "Hills"}
MODERATE_CLASSES = {"Long Run"}


def build_context_pack(db: Session, activity: Activity) -> dict:
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

    def _summarize(facts):
        return {
            "activity_count": len(facts),
            "total_distance_m": sum(f.distance_m for f in facts),
            "total_moving_time_s": sum(f.moving_time_s for f in facts),
            "total_effort": round(sum(f.effort_score or 0 for f in facts), 1),
        }

    # Training context: intensity distribution and recency signals
    training_context = _build_training_context(db, activity)

    pack = {
        "activity": {
            "date": activity.start_date.isoformat(),
            "name": activity.name,
            "type": activity.user_intent or activity.type,
            "distance_m": activity.distance_m,
            "moving_time_s": activity.moving_time_s,
            "avg_hr": activity.avg_hr,
            "max_hr": activity.max_hr,
            "avg_cadence": activity.avg_cadence,
            "elev_gain_m": activity.elev_gain_m,
        },
        "metrics": {
            "activity_class": metrics.activity_class if metrics else None,
            "effort_score": round(metrics.effort_score, 1) if metrics else None,
            "hr_drift": round(metrics.hr_drift, 1) if metrics and metrics.hr_drift else None,
            "pace_variability": (
                round(metrics.pace_variability, 1)
                if metrics and metrics.pace_variability
                else None
            ),
            "flags": metrics.flags if metrics else [],
            "confidence": metrics.confidence if metrics else "low",
            "confidence_reasons": metrics.confidence_reasons if metrics else [],
            "time_in_zones": metrics.time_in_zones if metrics else None,
            "zones_calibrated": zones_calibrated,
            "zones_basis": zones_basis,
            "efficiency_analysis": metrics.efficiency_analysis if metrics else None,
            "stops_analysis": metrics.stops_analysis if metrics else None,
            "interval_structure": metrics.interval_structure if metrics else None,
            "workout_match": metrics.workout_match if metrics else None,
            "interval_kpis": metrics.interval_kpis if metrics else None,
            "risk_level": metrics.risk_level if metrics else None,
            "risk_score": metrics.risk_score if metrics else None,
            "risk_reasons": metrics.risk_reasons if metrics else [],
        },
        "check_in": {
            "rpe": check_in.rpe if check_in else None,
            "pain_score": check_in.pain_score if check_in else None,
            "pain_location": check_in.pain_location if check_in else None,
            "sleep_quality": check_in.sleep_quality if check_in else None,
            "notes": check_in.notes if check_in else None,
        },
        "profile": {
            "goal_type": profile.goal_type if profile else None,
            "experience_level": profile.experience_level if profile else None,
            "weekly_days_available": profile.weekly_days_available if profile else None,
            "injury_notes": profile.injury_notes if profile else None,
            "max_hr": profile.max_hr if profile else None,
            "max_hr_source": getattr(profile, "max_hr_source", None) if profile else None,
            "current_weekly_km": profile.current_weekly_km if profile else None,
        },
        "training_context": training_context,
        "recent_training_summary": {
            "last_7d": _summarize(facts_7d),
            "last_28d": _summarize(facts_28d),
            "previous_28d": _summarize(facts_prev_28d),
        },
        "safety_rules": {
            "never_diagnose": True,
            "pain_severe_threshold": 7,
            "no_invented_facts": True,
        },
    }
    return pack


def _build_training_context(db: Session, activity: Activity) -> dict:
    """Compute intensity distribution and recency signals for the last 7 days."""
    activity_date = activity.start_date.date()
    start = activity_date - timedelta(days=7)

    # Query recent activities with metrics loaded
    recent = (
        db.execute(
            select(Activity)
            .where(
                Activity.user_id == activity.user_id,
                Activity.start_date >= start,
                Activity.start_date < activity.start_date,
                Activity.is_deleted == False,
            )
            .options(selectinload(Activity.metrics))
            .order_by(Activity.start_date.desc())
        )
        .scalars()
        .all()
    )

    easy = 0
    moderate = 0
    hard = 0
    days_since_last_hard = None

    for a in recent:
        ac = a.metrics.activity_class if a.metrics else "Easy Run"
        if ac in HARD_CLASSES:
            hard += 1
            if days_since_last_hard is None:
                delta = (activity_date - a.start_date.date()).days
                days_since_last_hard = delta
        elif ac in MODERATE_CLASSES:
            moderate += 1
        else:
            easy += 1

    return {
        "intensity_distribution_7d": {
            "easy": easy,
            "moderate": moderate,
            "hard": hard,
        },
        "days_since_last_hard": days_since_last_hard,
        "hard_sessions_this_week": hard,
    }


def hash_context_pack(pack: dict) -> str:
    """Deterministic SHA-256 hash of the context pack for reproducibility."""
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, default=str).encode()
    ).hexdigest()
