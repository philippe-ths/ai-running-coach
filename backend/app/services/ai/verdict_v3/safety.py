from typing import Dict, Any
from app.schemas import VerdictScorecardResponse, NextStepsResponse

def enforce_scorecard_safety(
    scorecard: VerdictScorecardResponse, 
    context_pack: Dict[str, Any]
) -> VerdictScorecardResponse:
    """
    Applies strict safety overrides to the scorecard.
    Rule: High pain or risk flags prevent "green" status.
    """
    
    # 1. Check Pain
    check_in = context_pack.get("check_in") or {}
    pain_score = check_in.get("pain_score") # API schema uses pain_score, slicer might differ slightly if raw vs schema, assume schema aligned
    # note: schema uses pain_score, but context_pack usually matches raw json. 
    # looking at context_pack_minimal.json, check_in has "pain_0_10". 
    # Let's handle both just in case, but prefer the context pack structure.
    
    if pain_score is None:
        pain_score = check_in.get("pain_0_10")

    is_high_pain = (pain_score is not None and pain_score >= 7)

    # 2. Check Risk Flags
    flags = context_pack.get("flags", [])
    has_risk_flag = any(
        f.get("severity") == "risk" or "injury" in f.get("code", "").lower() 
        for f in flags
    )

    should_downgrade = is_high_pain or has_risk_flag

    if should_downgrade and scorecard.headline.status == "green":
        # Force downgrade to amber at minimum, perhaps red if high pain
        new_status = "red" if is_high_pain else "amber"
        scorecard.headline.status = new_status
        scorecard.headline.sentence = f"[Safety Override] {scorecard.headline.sentence}"

    return scorecard


def enforce_next_steps_safety(
    next_steps: NextStepsResponse, 
    scorecard: VerdictScorecardResponse,
    context_pack: Dict[str, Any]
) -> NextStepsResponse:
    """
    Applies strict safety overrides to the schedule.
    Rule: Red/Amber status constrains tomorrow's intensity.
    """
    status = scorecard.headline.status.lower()
    tomorrow_text = next_steps.next_steps.tomorrow.lower()

    # Red Status -> Must be Rest or Very Easy
    if status == "red":
        is_safe = tomorrow_text.startswith("rest") or tomorrow_text.startswith("easy") or "off" in tomorrow_text
        if not is_safe:
            next_steps.next_steps.tomorrow = "Rest or very gentle active recovery only."

    # Amber Status -> Cannot be Quality/Hard
    elif status == "amber":
        is_quality = "speed" in tomorrow_text or "tempo" in tomorrow_text or "hard" in tomorrow_text or "interval" in tomorrow_text
        if is_quality:
             next_steps.next_steps.tomorrow = "Easy run or cross-training (prioritize recovery)."

    return next_steps
