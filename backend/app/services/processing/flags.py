from typing import List, Dict, Any, Optional
from app.models import Activity, CheckIn

def generate_flags(
    activity: Activity, 
    metric_data: Dict[str, Any], 
    history: List[Activity], 
    check_in: Optional[CheckIn] = None
) -> List[str]:
    """
    Generates warning/info flags based on data quality, intensity, and user feedback.
    """
    flags = []

    # --- Data Quality ---
    if not activity.avg_hr:
        flags.append("missing_heart_rate")
    
    # --- Intensity Check ---
    # "intensity_too_high_for_easy"
    # Logic: if labeled "Easy Run" but HR > 80% max (if max exists)
    is_easy = metric_data.get("activity_class") == "Easy Run"
    if is_easy and activity.avg_hr and activity.max_hr:
         if (activity.avg_hr / activity.max_hr) > 0.8:
             flags.append("intensity_too_high_for_easy")

    # --- Load Spike ---
    # Simple check: Is this run's effort > 2x average of last 7 runs?
    if history:
        # In a real impl, we'd query previously computed DerivedMetrics for history.
        # For MVP, we'll skip complex load calculation triggers here.
        pass

    # --- User Feedback ---
    if check_in:
        if check_in.pain_score and check_in.pain_score >= 4:
            flags.append("pain_reported")
        if check_in.pain_score and check_in.pain_score >= 7:
            flags.append("pain_severe")
    
    return flags
