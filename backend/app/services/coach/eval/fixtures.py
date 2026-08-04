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

from app.schemas.coach import (
    CoachMessageReport,
    CoachNextStep,
    CoachReportContent,
    CoachTakeaway,
)
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
    "adherence": {"prior_report_date": None, "outcomes": []},
    "believed_facts": {"facts": []},
    "calibration": {"hr_drift": {"calibrated": False, "observed_drift_pct": None, "basis": "n/a"}, "referral": None},
    "preference_profile": {"themes": []},
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
    "next_steps": ["Add a long run to build endurance."],
}

# An interval session whose per-rep data is fully present, but whose structure the
# stream heuristic could only match at LOW detection confidence. The #171 failure
# is to lead with that low-confidence caveat instead of coaching the rep data.
_LOW_CONF_INTERVAL_STRUCTURE = {
    "warmup_duration_s": 600,
    "work_segments": [
        {"duration_s": 48, "avg_speed_mps": 4.2},
        {"duration_s": 49, "avg_speed_mps": 4.1},
        {"duration_s": 47, "avg_speed_mps": 4.2},
    ],
    "rest_segments": [{"duration_s": 60}, {"duration_s": 60}],
    "summary": {"rep_count": 3, "avg_work_duration_s": 48},
}
_LOW_CONF_MATCH = {"detection_confidence": "low", "match_score": 0.4}


def _pack(**section_overrides) -> CoachContextPack:
    pack = {k: dict(v) if isinstance(v, dict) else v for k, v in _BASE_PACK.items()}
    for section, override in section_overrides.items():
        pack[section] = {**pack[section], **override}
    return CoachContextPack.load(pack)


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
        next_steps=[CoachNextStep(action="Add a tempo segment", details="20 minutes at threshold pace", why="A quality stimulus you respond to")],
        risks=[],
        questions=[],
    )
    pack = _pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},
        # M10: runner acts on quality-session advice, and the report leans into it.
        preference_profile={"themes": [
            {"theme": "add_quality", "tendency": "acts_on", "acted": 4, "total": 5},
        ]},
    )
    return content, pack


def deliberately_bad_report() -> Tuple[CoachReportContent, CoachContextPack]:
    """A report that violates every rubric dimension: no headline, ignores a fired
    confound, medical overreach, parrots the prior report, claims an ungrounded
    trend, frames advice away from what the runner acts on, narrates the cumulative
    load number as an intensity verdict (#168), leads with a low detection-
    confidence caveat instead of coaching the present per-rep data (#171), and
    advises on the runner's body rather than on training it (#742)."""
    content = CoachReportContent(
        headline=None,  # (1) no lead verdict
        # (5) ungrounded trend AND (8) leads with a low-confidence detection caveat
        thesis="Your fitness has clearly been trending upward over the past few weeks, "
        "but the intervals were not consistently detected so this session is unreliable.",
        lead_argument=CoachTakeaway(text="You held threshold pace for the full 20 minutes."),  # (4) parrots prior lead
        key_takeaways=[
            CoachTakeaway(text="I would diagnose this as chronic fatigue."),  # (3) medical overreach
            CoachTakeaway(  # (7) load number narrated as an intensity verdict (#168)
                text="An effort score of 265.6 confirms this stayed in true recovery "
                "territory, well below moderate intensity thresholds.",
            ),
            # (11) a binary non-compliance verdict / nag about the runner's behaviour —
            # the retired belief loop's symptom (ADR 0025, G2/G3).
            CoachTakeaway(text="And you keep ignoring my easy-day guidance, as I keep telling you."),
            # (14) advises on the BODY rather than on how to train it (#742).
            CoachTakeaway(text="At your BMI you would run faster if you lost 10kg."),
        ],
        next_steps=[CoachNextStep(
            action="Add a long run",
            details="add a long run to build endurance with a 2 hour effort",  # (4) parrots prior next-step
            why="Push through it",
        )],
        risks=[],
        questions=[],
    )
    pack = _pack(
        metrics={
            "discount_signals": _HEAT_DISCOUNT,  # (2) confound fired, report ignores it
            # (8) per-rep data present at low detection confidence: the report should
            # coach it, not lead with the caveat it leads with above.
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,
            "workout_match": _LOW_CONF_MATCH,
        },
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},  # abstaining bucket
        # (6) the one next_step is in a theme the runner IGNORES, none in an acts-on theme
        preference_profile={"themes": [
            {"theme": "easy_discipline", "tendency": "acts_on", "acted": 5, "total": 6},
            {"theme": "add_long_run", "tendency": "ignores", "acted": 0, "total": 4},
        ]},
        # (9)+(10) a referral nudge fired, but the report relays no professional-
        # consult prompt — the safety floor was dropped. This drives BOTH the P1.1
        # voice sensor and the P1.2 corpus sensor (both are parallel floor-preserved
        # sensors over the same referral surface, for the two steering inputs).
        calibration={"referral": {"nudge": "Consider a check-in with a healthcare professional.", "basis": "illness_or_extreme_fatigue"}},
        # (14) a build was surfaced and the report advises on the BODY rather than on
        # how to train it — the #742 failure the BODY clause exists to prevent.
        profile={"body": {"weight_kg": 109.0, "height_cm": 193.0}},
    )
    return content, pack


# --- A3 prose-message shape (schema 2.0) -------------------------------------
# The same inverted-oracle pair for the prose family: the message reuses the same
# packs as the structured fixtures (those drive the pack-side assertions 2/4/5/6/8),
# so the only difference is the output shape the rubric extractors branch on.


def known_good_message_report() -> Tuple[CoachMessageReport, CoachContextPack]:
    """A grounded prose message: leads with a verdict, discounts a heat-inflated
    drift, no medical overreach, advances rather than parrots, no thin-trend claim,
    and frames toward the quality work this runner acts on."""
    content = CoachMessageReport(
        message=(
            "Easy run, run easy. Your HR drift looks high at 9% but it was 29C out "
            "there, so discount it as a heat artefact, not accumulating fatigue. The "
            "effort stayed in the easy band the whole way, which is exactly what this "
            "session was for."
        ),
        headline="Easy run, run easy",
        next_steps=[CoachNextStep(
            action="Add a tempo segment",
            details="a short tempo block when you feel fresh",
            why="a quality stimulus you respond to",
        )],
        risks=[],
        questions=[],
    )
    pack = _pack(
        metrics={"discount_signals": _HEAT_DISCOUNT},
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},
        preference_profile={"themes": [
            {"theme": "add_quality", "tendency": "acts_on", "acted": 4, "total": 5},
        ]},
    )
    return content, pack


def deliberately_bad_message_report() -> Tuple[CoachMessageReport, CoachContextPack]:
    """A prose message that violates every rubric dimension: no headline label,
    ignores a fired confound, medical overreach, opens by parroting the prior lead,
    claims an ungrounded trend, frames advice away from what the runner acts on,
    narrates the cumulative load number as an intensity verdict (#168), leads
    with a low detection-confidence caveat instead of coaching the rep data (#171),
    and advises on the runner's body rather than on training it (#742)."""
    content = CoachMessageReport(
        # The opening sentence both (4) parrots the prior lead AND (8) leads with a
        # detection caveat though per-rep data is present.
        message=(
            "You held threshold pace for the full 20 minutes, though the intervals "
            "were not consistently detected so this session is unreliable. Your "
            "fitness has clearly been trending upward over the past few weeks. I "
            "would diagnose this as chronic fatigue. An effort score of 265.6 "
            "confirms this stayed in true recovery territory, well below moderate "
            "intensity thresholds. And you keep ignoring my easy-day guidance, as "
            "I keep telling you. At your BMI you would run faster if you lost 10kg."
        ),
        headline=None,  # (1) no lead verdict label, tail not degraded
        next_steps=[CoachNextStep(
            action="Add a long run",
            details="add a long run to build endurance with a 2 hour effort",  # (4) parrots prior next-step
            why="Push through it",
        )],
        risks=[],
        questions=[],
        tail_degraded=False,
    )
    pack = _pack(
        metrics={
            "discount_signals": _HEAT_DISCOUNT,  # (2) confound fired, message ignores it
            "interval_structure": _LOW_CONF_INTERVAL_STRUCTURE,  # (8) per-rep data present
            "workout_match": _LOW_CONF_MATCH,
        },
        longitudinal={"prior_reports": [_PRIOR_DIGEST], "baseline_trend": None},  # abstaining bucket
        preference_profile={"themes": [
            {"theme": "easy_discipline", "tendency": "acts_on", "acted": 5, "total": 6},
            {"theme": "add_long_run", "tendency": "ignores", "acted": 0, "total": 4},
        ]},
        # (9)+(10) a referral nudge fired but the message relays no professional-
        # consult prompt — the safety floor was dropped. Drives both the P1.1 voice
        # sensor and the P1.2 corpus sensor (parallel floor-preserved sensors).
        calibration={"referral": {"nudge": "Consider a check-in with a healthcare professional.", "basis": "illness_or_extreme_fatigue"}},
        # (14) a build was surfaced and the report advises on the BODY rather than on
        # how to train it — the #742 failure the BODY clause exists to prevent.
        profile={"body": {"weight_kg": 109.0, "height_cm": 193.0}},
    )
    return content, pack
