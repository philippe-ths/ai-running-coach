"""
Context pack builder — assembles all facts the LLM needs into a single JSON dict.

No computation happens here. This module only gathers and shapes existing data
from the database (activity, metrics, check-in, profile, trends).
"""

import hashlib
import json
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Activity, UserProfile
from app.services.trends import _query_activity_facts


def build_context_pack(db: Session, activity: Activity) -> dict:
    """Assemble all facts the LLM needs. No computation, just data gathering."""
    metrics = activity.metrics
    check_in = getattr(activity, "check_in", None)
    profile: Optional[UserProfile] = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == activity.user_id)
        .first()
    )

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

    pack = {
        "activity": {
            "date": activity.start_date.isoformat(),
            "type": activity.user_intent or activity.type,
            "distance_m": activity.distance_m,
            "moving_time_s": activity.moving_time_s,
            "avg_hr": activity.avg_hr,
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
        },
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


def hash_context_pack(pack: dict) -> str:
    """Deterministic SHA-256 hash of the context pack for reproducibility."""
    return hashlib.sha256(
        json.dumps(pack, sort_keys=True, default=str).encode()
    ).hexdigest()
