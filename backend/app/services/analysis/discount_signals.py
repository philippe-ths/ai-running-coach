"""Deterministic confounder stage (N4).

Emits a pipeline-owned, templated annotation describing whether an activity's
HR drift is likely inflated by external confounders (heat, terrain, stimulant
use) rather than genuine cardiac fatigue. This is a relay, never LLM discretion:
the coach must honor the annotation but never invent confounders not listed here.
"""

from typing import Optional

HEAT_TEMP_C = 25.0


def compute_discount_signals(
    average_temp: Optional[float],
    is_hilly: Optional[bool],
    hr_drift: Optional[float],
    stimulant_use: Optional[bool],
) -> Optional[dict]:
    """Compute the discount-signals annotation for an activity.

    Args:
        average_temp: degrees C from Strava raw_summary, or None if unrecorded.
        is_hilly: terrain modifier from the classification axes, or None.
        hr_drift: HR drift as a percent, or None if not computed.
        stimulant_use: opt-in physiology flag from the user profile, or None.

    Returns None when there is nothing to discount, otherwise a templated dict.
    """
    if hr_drift is None:
        return None  # nothing to discount

    # Strava sends average_temp as a number, but guard a stray non-numeric value
    # rather than raising mid-pipeline. An unusable value degrades to "unknown"
    # (never a fabricated heat confound).
    if average_temp is not None:
        try:
            average_temp = float(average_temp)
        except (TypeError, ValueError):
            average_temp = None

    inflators = []
    if average_temp is not None and average_temp >= HEAT_TEMP_C:
        inflators.append("heat")
    if is_hilly:
        inflators.append("terrain")
    if stimulant_use:
        inflators.append("stimulant")

    temp_known = average_temp is not None
    if not inflators and temp_known:
        return None  # clean run, temp known -> drift trustworthy

    confidence = "high" if temp_known else "low"  # never fabricate heat we never measured

    if inflators:
        interpretation = (
            f"This HR drift is likely inflated by {', '.join(inflators)}; "
            "discount it as a fatigue signal."
        )
    else:
        interpretation = (
            "Temperature was not recorded, so heat inflation of this HR drift "
            "cannot be ruled out; treat the drift cautiously."
        )

    return {
        "hr_drift_pct": round(hr_drift, 1),
        "likely_inflated_by": inflators,
        "temperature_c": round(average_temp, 1) if temp_known else None,
        "confidence": confidence,
        "interpretation": interpretation,
    }
