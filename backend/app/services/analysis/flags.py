from typing import List, Dict, Any, Optional
from app.models import Activity, CheckIn, DerivedMetric


def generate_flags(
    activity: Activity,
    metric_data: Dict[str, Any],
    history: List[Activity],
    check_in: Optional[CheckIn] = None,
    history_metrics: Optional[List[DerivedMetric]] = None,
) -> List[str]:
    """
    Generates warning/info flags based on data quality, intensity,
    fatigue signals, load spikes, and user feedback.

    All flag names align with SPEC.md taxonomy.
    """
    flags = []

    # --- Data Quality ---
    if not activity.avg_hr:
        flags.append("data_low_confidence_hr")

    # --- Intensity Mismatch ---
    # Stated intent was easy/recovery but the measured effort was hard. This is
    # the intent-vs-executed gap (ADR 0007): the axes always reflect the data, so
    # a mismatch is only meaningful against what the runner said they intended.
    intent = (activity.user_intent or "").lower()
    effort = metric_data.get("effort")
    if intent in ("easy run", "easy", "recovery") and effort in ("tempo", "hard"):
        flags.append("intensity_mismatch")

    # --- Fatigue Possible (was cardiac_drift_high) ---
    drift = metric_data.get("hr_drift")
    if drift is not None and drift > 5.0:
        flags.append("fatigue_possible")

    # --- Pace Unstable ---
    # A continuous high-effort run is expected to hold a steady pace; high
    # variability there signals instability (intervals are excluded — their
    # variability is by design).
    pace_var = metric_data.get("pace_variability")
    if (
        pace_var is not None
        and pace_var > 15.0
        and metric_data.get("structure") == "continuous"
        and metric_data.get("effort") in ("tempo", "hard")
    ):
        flags.append("pace_unstable")

    # --- Load Spike ---
    if history_metrics:
        recent_efforts = [
            m.effort_score
            for m in history_metrics[:7]
            if m.effort_score is not None
        ]
        if recent_efforts:
            mean_effort = sum(recent_efforts) / len(recent_efforts)
            current_effort = metric_data.get("effort_score")
            if current_effort and mean_effort > 0 and current_effort > 1.8 * mean_effort:
                flags.append("load_spike")

    # --- Illness or Extreme Fatigue ---
    if check_in:
        rpe = check_in.rpe or 0
        sleep = check_in.sleep_quality or 10  # default high to avoid false positives
        pain = check_in.pain_score or 0
        if rpe >= 8 and sleep <= 2 and pain >= 5:
            flags.append("illness_or_extreme_fatigue")

    # --- User Feedback ---
    if check_in:
        if check_in.pain_score and check_in.pain_score >= 4:
            flags.append("pain_reported")
        if check_in.pain_score and check_in.pain_score >= 7:
            flags.append("pain_severe")

    return flags
