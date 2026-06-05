"""SYNTHETIC eval fixtures — NOT production data (aiw-ground-truth trust level 5).

These hand-authored reports are the inverted oracle for the harness itself: the
``known_good_report`` must pass every applicable rubric assertion and the
``deliberately_bad_report`` must fail the intended dimensions. They let
``make eval-selftest`` validate the harness with no seeded DB and no API key.

Do not treat these as examples of real coach output or as ground truth about
runner behaviour. They exist only to exercise the scorer's branches.
"""

from __future__ import annotations

from typing import Tuple

from app.schemas.coach import CoachNextStep, CoachReportContent, CoachTakeaway
from app.schemas.coach_context import CoachContextPack

_BASE_PACK = {
    "activity": {
        "date": "2026-02-15T10:00:00+00:00", "name": "Run", "type": "Run",
        "distance_m": 10000, "moving_time_s": 3600,
        "avg_hr": 150.0, "max_hr": 175.0, "avg_cadence": 170.0, "elev_gain_m": 50.0,
    },
    "metrics": {
        "headline": "Easy run", "effort": "easy", "duration_class": "standard",
        "structure": "continuous", "is_hilly": False, "is_race": False,
        "effort_score": 3.0, "hr_drift": 9.0, "pace_variability": None,
        "flags": [], "confidence": "high", "confidence_reasons": [],
        "time_in_zones": None, "zones_calibrated": True, "zones_basis": "user_user_entered",
        "efficiency_analysis": None, "stops_analysis": None, "interval_structure": None,
        "workout_match": None, "interval_kpis": None,
        "risk_level": None, "risk_score": None, "risk_reasons": [],
        "training_context": None, "discount_signals": None,
    },
    "check_in": {"rpe": 6, "pain_score": 0, "pain_location": None, "sleep_quality": 4, "notes": None},
    "profile": {
        "goal_type": None, "experience_level": None, "weekly_days_available": None,
        "injury_notes": None, "max_hr": None, "max_hr_source": None, "current_weekly_km": None,
    },
    "recent_training_summary": {
        "last_7d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
        "last_28d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
        "previous_28d": {"activity_count": 0, "total_distance_m": 0, "total_moving_time_s": 0, "total_effort": 0.0},
    },
    "longitudinal": {"prior_reports": [], "baseline_trend": None},
    "perceived_effort": {
        "rpe": None, "effort_axis": "easy", "effort_score": 3.0,
        "divergence": None, "divergence_direction": None,
        "hr_confounded": False, "recommended_weighting": "hr_only", "pain_trend": None,
    },
    "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
}

_HEAT_DISCOUNT = {
    "hr_drift_pct": 9.0,
    "likely_inflated_by": ["heat"],
    "temperature_c": 29.0,
    "confidence": "high",
    "interpretation": "This HR drift is likely inflated by heat; discount it as a fatigue signal.",
}

_PRIOR_DIGEST = {
    "activity_date": "2026-02-08T10:00:00+00:00",
    "headline": "Tempo run",
    "lead_argument": "You held threshold pace for the full 20 minutes.",
    "next_steps": ["Recover with two easy runs before the next quality session."],
}


def _pack(**section_overrides) -> CoachContextPack:
    pack = {k: dict(v) if isinstance(v, dict) else v for k, v in _BASE_PACK.items()}
    for section, override in section_overrides.items():
        pack[section] = {**pack[section], **override}
    return CoachContextPack.model_validate(pack)


def known_good_report() -> Tuple[CoachReportContent, CoachContextPack]:
    """A grounded report: leads with a headline, discounts a heat-inflated drift,
    no medical overreach, advances the prior narrative, makes no thin-trend claim."""
    content = CoachReportContent(
        headline="Easy run, run easy",
        thesis="Your HR drift looks high but it was 29C, so discount it as heat, not fatigue.",
        lead_argument=CoachTakeaway(text="Effort stayed in the easy band despite the heat."),
        key_takeaways=[
            CoachTakeaway(text="Comfortable aerobic effort throughout."),
            CoachTakeaway(text="The drift is a heat artefact, not accumulating fatigue."),
        ],
        next_steps=[CoachNextStep(action="Long run", details="90 min easy on Sunday", why="Build aerobic base")],
        risks=[],
        questions=[],
    )
    pack = _pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},
    )
    return content, pack


def deliberately_bad_report() -> Tuple[CoachReportContent, CoachContextPack]:
    """A report that violates every rubric dimension: no headline, ignores a fired
    confound, medical overreach, parrots the prior report, claims an ungrounded trend."""
    content = CoachReportContent(
        headline=None,  # (1) no lead verdict
        thesis="Your fitness has clearly been trending upward over the past few weeks.",  # (5) ungrounded trend
        lead_argument=CoachTakeaway(text="You held threshold pace for the full 20 minutes."),  # (4) parrots prior lead
        key_takeaways=[CoachTakeaway(text="I would diagnose this as chronic fatigue.")],  # (3) medical overreach
        next_steps=[CoachNextStep(
            action="Recover",
            details="Recover with two easy runs before the next quality session.",  # (4) parrots prior next-step
            why="Push through it",
        )],
        risks=[],
        questions=[],
    )
    pack = _pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},  # (2) confound fired, report ignores it
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},  # abstaining bucket
    )
    return content, pack
