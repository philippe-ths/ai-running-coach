from typing import Dict, Any
from app.schemas import VerdictScorecardResponse, NextStepsResponse

def enforce_scorecard_safety(
    scorecard: VerdictScorecardResponse, 
    context_pack: Dict[str, Any]
) -> VerdictScorecardResponse:
    """
    Applies strict safety overrides to the scorecard.
    Rule: High pain or risk flags prevent "green" status.
    Strategies:
    - If risk detected, force the "Risk / recoverability" item to warn/fail.
    """
    
    # 1. Check Pain
    check_in = context_pack.get("check_in") or {}
    pain_score = check_in.get("pain_score") 
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

    if should_downgrade:
        # Find the Risk item
        risk_item = next(
            (item for item in scorecard.scorecard if item.item == "Risk / recoverability"), 
            None
        )
        
        if risk_item:
            # Downgrade logic
            target_rating = "fail" if is_high_pain else "warn"
            
            # Only downgrade if currently better (e.g. don't change 'fail' to 'warn')
            current_severity = {"fail": 3, "warn": 2, "unknown": 1, "ok": 0}
            if current_severity.get(risk_item.rating, 0) < current_severity.get(target_rating, 0):
                risk_item.rating = target_rating
                risk_item.reason = f"[Safety Override] High pain/risk detected. {risk_item.reason}"

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
    # Derive provisional status from scorecard items
    failures = [i for i in scorecard.scorecard if i.rating == "fail"]
    warnings = [i for i in scorecard.scorecard if i.rating == "warn"]
    
    status = "green"
    if failures:
        status = "red"
    elif warnings:
        status = "amber"

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
