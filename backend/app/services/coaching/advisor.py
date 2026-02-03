from typing import List, Dict, Any, Optional
from app.models import Activity, DerivedMetric, UserProfile, CheckIn

# --- Helpers ---
def _format_duration(minutes: int) -> str:
    return f"{minutes} min"

def _get_next_run_prescription(scenario: str, last_duration: float) -> dict:
    """
    Returns structured next_run dict based on scenario.
    last_duration is in minutes.
    """
    base_duration = max(30, int(last_duration * 0.8)) # Default to slightly shorter than current if unknown context
    
    if scenario == "stop":
        return {
            "duration": "0 min",
            "type": "Rest",
            "intensity": "None",
            "description": "Rest day or seek medical advice if pain persists."
        }
    elif scenario == "recovery":
        return {
            "duration": "30 min",
            "type": "Recovery",
            "intensity": "RPE 1-2 (Very Easy)",
            "description": "Walking or very slow jog. Heart rate Z1 if available."
        }
    elif scenario == "easy_conservative":
        return {
            "duration": f"{base_duration} min",
            "type": "Easy",
            "intensity": "RPE 3-4 (Conversational)",
            "description": "Keep it purely conversational. No huffing and puffing."
        }
    elif scenario == "strides":
        return {
            "duration": "45 min",
            "type": "Easy + Strides",
            "intensity": "RPE 3-4 + Bursts",
            "description": "40 min easy jog, then 4-6 x 20s fast runs (smooth accels). Full rest between."
        }
    # Default
    return {
        "duration": "40-50 min",
        "type": "Easy",
        "intensity": "RPE 3-4",
        "description": "Standard base mileage run."
    }

def generate_advice_structure(
    activity: Activity,
    metrics: DerivedMetric,
    profile: Optional[UserProfile],
    history: List[Activity],
    check_in: Optional[CheckIn]
) -> Dict[str, Any]:
    
    flags = metrics.flags if metrics.flags else []
    pain_score = check_in.pain_score if (check_in and check_in.pain_score) else 0
    activity_class = metrics.activity_class or "Unknown"
    
    # 1. Defaults
    verdict = "Solid effort. Banked the miles."
    week_adjustment = "No major changes needed."
    warnings = []
    question = None
    evidence = []
    
    # Duration in minutes
    duration_min = activity.moving_time_s / 60.0
    
    # 2. Logic Gates (Priority Order)
    
    # A) Severe Pain
    if "pain_severe" in flags or pain_score >= 7:
        verdict = "Stop and Assess."
        evidence.append(f"Reported high pain score ({pain_score}/10).")
        warnings.append("High pain detected. Do not run through sharp pain.")
        next_run = _get_next_run_prescription("stop", duration_min)
        week_adjustment = "Clear the schedule until pain subsides."
        question = "Has this pain occurred before?"

    # B) Load Spike / Fatigue
    elif "load_spike" in flags or "fatigue_possible" in flags:
        verdict = "Manage Fatigue."
        evidence.append("Recent training load is significantly higher than baseline.")
        warnings.append("Risk of overuse injury is elevated.")
        next_run = _get_next_run_prescription("recovery", duration_min)
        week_adjustment = "Reduce volume by 20% this week."

    # C) Intensity Discipline (Easy run too hard)
    elif "intensity_too_high_for_easy" in flags:
        verdict = "Too Fast for Easy."
        evidence.append("Heart rate was above Z2 for an 'Easy' labeled run.")
        evidence.append("Perceived effort might be lower than physiological cost.")
        next_run = _get_next_run_prescription("easy_conservative", duration_min)
        week_adjustment = "Prioritize discipline on easy days."
        question = "Did you feel like you were pushing the pace?"

    # D) Propose Stimulus (Default happy path)
    else:
        # Check if we should add strides
        # Simple heuristic: if 'Easy Run' and short-ish, suggest variance next time
        if activity_class == "Easy Run" and duration_min < 60:
            verdict = "Good Base Building."
            evidence.append("Consistent easy pace maintained.")
            next_run = _get_next_run_prescription("strides", duration_min)
        else:
            verdict = "Strong Run."
            evidence.append(f"Complete {activity_class} session.")
            next_run = _get_next_run_prescription("easy_conservative", duration_min)

    # 3. Construct Full Text
    # Reuse the helper to ensure consistency
    result_dict = {
        "verdict": verdict,
        "evidence": evidence,
        "next_run": next_run,
        "week_adjustment": week_adjustment,
        "warnings": warnings,
        "question": question
    }
    
    result_dict["full_text"] = construct_full_text(result_dict)
    
    return result_dict

def construct_full_text(data: Dict[str, Any]) -> str:
    """
    Helper to build the Markdown representation from structured advice data.
    Used by both rule-based and AI-based generators.
    """
    warnings = data.get("warnings", [])
    evidence = data.get("evidence", [])
    next_run = data.get("next_run", {})
    verdict = data.get("verdict", "")
    week_adj = data.get("week_adjustment", "")

    full_text = f"**Verdict:** {verdict}\n\n"
    full_text += "**Evidence:**\n" + "\n".join([f"- {e}" for e in evidence]) + "\n\n"
    
    # Handle next_run safely
    nr_dur = next_run.get('duration', 'N/A')
    nr_type = next_run.get('type', 'N/A')
    nr_int = next_run.get('intensity', 'N/A')
    nr_desc = next_run.get('description', '')
    
    full_text += f"**Next Run:** {nr_dur} {nr_type} ({nr_int})\n"
    full_text += f"_{nr_desc}_\n\n"
    
    if warnings:
        full_text += "**Warnings:**\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"
        
    full_text += f"**Adjustment:** {week_adj}"
    
    return full_text
