from typing import Dict, Any, List, Optional
from app.schemas import VerdictScorecardResponse, LeverResponse, StoryResponse, NextStepsResponse

def _get_signals(cp: Dict[str, Any]) -> Dict[str, List[str]]:
    return {
        "available_signals": cp.get("available_signals", []),
        "missing_signals": cp.get("missing_signals", [])
    }

def slice_for_scorecard(context_pack: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepares context for the initial analysis (Rule Check + Scorecard).
    Needs broad access to metrics and flags to evaluate performance against intent.
    """
    return {
        "activity_summary": {
            k: v for k, v in context_pack.get("activity", {}).items()
            if k in ["type", "distance_m", "moving_time_s", "avg_hr", "avg_pace_s_per_km", "elevation_gain_m"]
        },
        "athlete_profile": {
            k: v for k, v in context_pack.get("athlete", {}).items()
            if k in ["goal", "experience_level"]
        },
        "derived_metrics": context_pack.get("derived_metrics", []),
        "analysis_flags": context_pack.get("flags", []),
        "check_in": context_pack.get("check_in", {}),
        **_get_signals(context_pack)
    }

def slice_for_story(context_pack: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepares context for the narrative story.
    Focuses on 'what happened' evidences and subjective feel.
    Avoids raw streams, uses summarized evidence.
    """
    # Extract evidence strings from metrics
    metric_evidence = [
        f"{m.get('key')}: {m.get('evidence')}" 
        for m in context_pack.get("derived_metrics", []) 
        if m.get("evidence")
    ]
    
    # Extract flag messages
    flag_messages = [
        f"{f.get('severity').upper()}: {f.get('message')} ({f.get('evidence')})"
        for f in context_pack.get("flags", [])
    ]

    return {
        "activity_context": {
            "name": context_pack.get("activity", {}).get("name"),
            "time_of_day": context_pack.get("activity", {}).get("start_time"),
            "duration": context_pack.get("activity", {}).get("moving_time_s"),
        },
        "subjective": {
            "rpe": context_pack.get("check_in", {}).get("rpe_0_10"),
            "notes": context_pack.get("check_in", {}).get("notes"),
        },
        "key_events_and_data": metric_evidence + flag_messages,
        **_get_signals(context_pack)
    }

def slice_for_lever(
    context_pack: Dict[str, Any], 
    scorecard_result: VerdictScorecardResponse
) -> Dict[str, Any]:
    """
    Prepares context for identifying the single biggest lever for improvement.
    Needs the failing scorecard items and diagnostic metrics.
    """
    # Identify weak areas from scorecard
    weak_areas = [
        item for item in scorecard_result.scorecard 
        if item.rating in ["warn", "fail", "unknown"]
    ]
    
    return {
        "scorecard_weaknesses": [item.model_dump() for item in weak_areas],
        "diagnostics": {
            "flags": context_pack.get("flags", []),
            "metrics": context_pack.get("derived_metrics", [])
        },
        "athlete_level": context_pack.get("athlete", {}).get("experience_level"),
        **_get_signals(context_pack)
    }

def slice_for_next_steps(
    context_pack: Dict[str, Any], 
    scorecard_result: VerdictScorecardResponse, 
    lever_result: LeverResponse
) -> Dict[str, Any]:
    """
    Prepares context for 'Next Steps'.
    Focuses on recovery state, recent load, and the prescriped lever.
    """
    # Derive provisional status from scorecard items since Executive Summary hasn't run yet
    failures = [i for i in scorecard_result.scorecard if i.rating == "fail"]
    warnings = [i for i in scorecard_result.scorecard if i.rating == "warn"]
    
    # Logic: Any fail -> RED, Any warn -> AMBER, else GREEN
    verdict_status = "green"
    if failures:
        verdict_status = "red"
    elif warnings:
        verdict_status = "amber"

    return {
        "current_status": {
            "verdict": verdict_status,
            "fatigue_check": context_pack.get("check_in", {}),
        },
        "recent_load": context_pack.get("last_7_days", {}),
        "prescribed_focus": lever_result.lever.category,
        "athlete_schedule": {
            "goal": context_pack.get("athlete", {}).get("goal"),
            # In a real app we might pass "next_scheduled_run" here if available
        },
        **_get_signals(context_pack)
    }

def slice_for_question(
    context_pack: Dict[str, Any], 
    scorecard_result: VerdictScorecardResponse
) -> Dict[str, Any]:
    """
    Prepares context for the closing engagement question.
    """
    # Derive theme if headline is missing
    theme = "Run Analysis"
    if scorecard_result.headline:
        theme = scorecard_result.headline.sentence
    else:
        # Fallback theme based on worst performing metric
        failures = [i for i in scorecard_result.scorecard if i.rating == "fail"]
        if failures:
            theme = f"Addressing {failures[0].item}"

    return {
        "user_notes": context_pack.get("check_in", {}).get("notes"),
        "verdict_theme": theme,
        "pain_points": [
            item.item for item in scorecard_result.scorecard 
            if item.rating in ["fail", "warn"]
        ],
        **_get_signals(context_pack)
    }

def slice_for_summary(
    context_pack: Dict[str, Any],
    scorecard: VerdictScorecardResponse,
    lever: LeverResponse,
    story: StoryResponse,
    next_steps: NextStepsResponse
) -> Dict[str, Any]:
    """
    Prepares context for the Executive Summary / Final Verdict.
    Has access to EVERYTHING.
    """
    return {
        "athlete": context_pack.get("athlete", {}),
        "activity_summary": {k:v for k,v in context_pack.get("activity", {}).items() if k != "streams"}, # no streams
        "analysis_flags": context_pack.get("flags", []),
        "check_in": context_pack.get("check_in", {}),
        "generated_insights": {
            "scorecard": [s.model_dump() for s in scorecard.scorecard],
            "why_it_matters": scorecard.why_it_matters,
            "story": story.run_story.model_dump(),
            "lever": lever.lever.model_dump(),
            "next_steps": next_steps.next_steps.model_dump()
        }
    }
