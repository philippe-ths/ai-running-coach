"""
Deterministic risk score — additive points system based on flags, check-in, and training context.

Risk levels:
- green (0-1 pts): normal, no concerns
- amber (2-3 pts): caution, mention in coaching
- red   (4+ pts): stop/rest recommendation
"""

from typing import Any, Dict, List, Optional, Tuple


# Flag → points mapping
FLAG_POINTS = {
    "load_spike": 3,
    "fatigue_possible": 1,
    "pain_reported": 2,
    "pain_severe": 4,
    "illness_or_extreme_fatigue": 4,
}


def compute_risk_score(
    flags: List[str],
    check_in: Optional[Dict[str, Any]] = None,
    training_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute a deterministic risk score from flags, check-in data, and training context.

    Returns {"risk_level": "green"|"amber"|"red", "risk_score": int, "risk_reasons": [str]}
    """
    points = 0
    reasons: List[str] = []

    # Flag-based points
    for flag in flags:
        if flag in FLAG_POINTS:
            pts = FLAG_POINTS[flag]
            points += pts
            reasons.append(f"{flag} (+{pts})")

    # Check-in combo: poor sleep + high RPE
    if check_in:
        sleep = check_in.get("sleep_quality")
        rpe = check_in.get("rpe")
        if sleep is not None and rpe is not None and sleep <= 2 and rpe >= 8:
            points += 2
            reasons.append("poor_sleep_high_rpe (+2)")

    # Training load: 2+ hard sessions in last 3 days
    if training_context:
        hard = training_context.get("hard_sessions_this_week", 0)
        days_since = training_context.get("days_since_last_hard")
        if hard >= 2 and days_since is not None and days_since <= 3:
            points += 1
            reasons.append("consecutive_hard_sessions (+1)")

    # Determine level
    if points >= 4:
        level = "red"
    elif points >= 2:
        level = "amber"
    else:
        level = "green"

    return {
        "risk_level": level,
        "risk_score": points,
        "risk_reasons": reasons,
    }
