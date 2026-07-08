"""
Workout matching — compares planned workout (from user input) with detected intervals.

Produces a match_score (0–1) and detection_confidence level with reasons,
used to gate what claims the LLM is allowed to make about interval execution.
"""

from typing import Any, Dict, List, Optional

import numpy as np

# Deadbands so a trivial rep-to-rep wobble is read as "holding"/"flat" rather
# than manufacturing a fade or a fatigue trend (#637).
_PACE_FADE_DEADBAND_S = 5      # s/km first->last before it counts as fade/negative-split
_FLOOR_TREND_DEADBAND_PCT = 3  # % of max the restart floor must move to count as a trend


def match_planned_to_detected(
    interval_structure: Optional[dict],
    planned_workout: Optional[dict],
) -> dict:
    """
    Compare a user's planned workout to what the interval detector found.

    Args:
        interval_structure: output from detect_intervals() (or None)
        planned_workout: structured user input, e.g.
            {"reps_planned": 8, "rep_distance_m": 400, "rest_s": 60}

    Returns dict with:
        match_score: 0.0–1.0 (how well detected matches planned)
        detection_confidence: "high" | "medium" | "low"
        confidence_reasons: list of machine-readable reason strings
        detected_workout: summary of what was actually detected
    """
    result = {
        "match_score": None,
        "detection_confidence": "low",
        "confidence_reasons": [],
        "detected_workout": None,
    }

    if not interval_structure:
        result["confidence_reasons"].append("no_intervals_detected")
        return result

    summary = interval_structure.get("summary", {})
    work_segments = interval_structure.get("work_segments", [])

    if not work_segments:
        result["confidence_reasons"].append("no_work_segments")
        return result

    # Build detected workout summary
    distances = [w.get("distance_m") for w in work_segments if w.get("distance_m")]
    durations = [w["duration_s"] for w in work_segments]

    detected = {
        "reps_detected": summary.get("rep_count", len(work_segments)),
        "rep_distance_mean_m": round(float(np.mean(distances)), 1) if distances else None,
        "rep_distance_cv": _cv_percent(distances) if distances else None,
        "rep_duration_mean_s": round(float(np.mean(durations)), 1),
        "rep_duration_cv": summary.get("work_duration_cv"),
        "total_work_time_s": summary.get("total_work_time_s"),
        "total_rest_time_s": summary.get("total_rest_time_s"),
        "work_to_rest_ratio": summary.get("work_to_rest_ratio"),
        "consistency_score": summary.get("consistency_score"),
    }
    result["detected_workout"] = detected

    # Check for outliers in rep distances
    if distances and len(distances) >= 3:
        dist_arr = np.array(distances)
        median = np.median(dist_arr)
        if median > 0:
            deviations = np.abs(dist_arr - median) / median
            outlier_count = int(np.sum(deviations > 0.5))  # >50% from median
            if outlier_count > 0:
                result["confidence_reasons"].append(
                    f"distance_outliers_{outlier_count}_of_{len(distances)}"
                )

    # Check rep distance CV
    if detected["rep_distance_cv"] is not None and detected["rep_distance_cv"] > 30:
        result["confidence_reasons"].append("high_rep_distance_variability")

    # Check rep duration CV
    if detected["rep_duration_cv"] is not None and detected["rep_duration_cv"] > 30:
        result["confidence_reasons"].append("high_rep_duration_variability")

    # Recorded-laps ground-truth treatment: applies regardless of whether a
    # planned workout is present. The runner's own lap marks settle the detection
    # question -- confidence is always "high". Rep-to-rep spread is the runner's
    # actual workout (a ladder, pyramid, fartlek), not detection uncertainty, so
    # variability/outlier reasons are stripped here before they can leak downstream.
    # When no plan is present the laps are also a perfect de-facto plan (match_score
    # 1.0). When a plan IS present, match_score is left to the plan-scoring block
    # below so it can reflect plan adherence -- detection certainty is still "high"
    # because the detection question is already settled by the lap marks.
    if interval_structure.get("source") == "recorded_laps":
        result["confidence_reasons"] = [
            r
            for r in result["confidence_reasons"]
            if "variability" not in r and "outlier" not in r
        ]
        result["detection_confidence"] = "high"
        if not planned_workout:
            result["confidence_reasons"].append("no_planned_workout")
            # The recorded laps are a perfect de-facto plan, so match_score is
            # 1.0 -- keeps the prompt's "high AND match_score >= 0.8" gate and
            # the deterministic validator consistent for the lap-sourced case.
            result["match_score"] = 1.0
            return result
        # Fall through to plan scoring so match_score can reflect adherence.

    elif not planned_workout:
        # Stream-derived, no plan: base detection confidence on consistency.
        result["confidence_reasons"].append("no_planned_workout")
        consistency = summary.get("consistency_score", "unknown")
        if consistency == "high" and not any(
            "outlier" in r for r in result["confidence_reasons"]
        ):
            result["detection_confidence"] = "medium"
        else:
            result["detection_confidence"] = "low"
        return result

    # With a planned workout: compute match_score
    scores = []
    reasons = result["confidence_reasons"]

    # Rep count match
    reps_planned = planned_workout.get("reps_planned")
    reps_detected = detected["reps_detected"]
    if reps_planned and reps_detected:
        rep_ratio = min(reps_planned, reps_detected) / max(reps_planned, reps_detected)
        scores.append(rep_ratio)
        if reps_planned != reps_detected:
            reasons.append(
                f"rep_count_mismatch_planned_{reps_planned}_detected_{reps_detected}"
            )

    # Rep distance match
    rep_dist_planned = planned_workout.get("rep_distance_m")
    rep_dist_detected = detected["rep_distance_mean_m"]
    if rep_dist_planned and rep_dist_detected:
        dist_ratio = min(rep_dist_planned, rep_dist_detected) / max(
            rep_dist_planned, rep_dist_detected
        )
        scores.append(dist_ratio)
        if dist_ratio < 0.7:
            reasons.append("rep_distance_mismatch")

    # Rest duration match
    rest_planned = planned_workout.get("rest_s")
    avg_rest = interval_structure.get("summary", {}).get("avg_rest_duration_s")
    if rest_planned and avg_rest:
        rest_ratio = min(rest_planned, avg_rest) / max(rest_planned, avg_rest)
        scores.append(rest_ratio)
        if rest_ratio < 0.5:
            reasons.append("rest_duration_mismatch")

    # Total work time plausibility
    total_work = detected["total_work_time_s"]
    if reps_planned and rep_dist_planned and total_work:
        # Rough expected work time: reps * (distance / ~4 m/s for typical speed)
        expected_work_s = reps_planned * (rep_dist_planned / 4.0)
        work_ratio = min(expected_work_s, total_work) / max(expected_work_s, total_work)
        if work_ratio < 0.4:
            reasons.append("work_time_implausible_for_plan")
            scores.append(work_ratio)

    # Compute match score
    if scores:
        match_score = round(float(np.mean(scores)), 2)
    else:
        match_score = 0.0

    result["match_score"] = match_score

    # For recorded_laps the detection confidence was already locked to "high"
    # above -- plan adherence (match_score) is a separate axis and must not
    # downgrade detection certainty that the lap marks have already settled.
    if interval_structure.get("source") != "recorded_laps":
        # Derive detection confidence from match score and reasons
        critical_reasons = [
            r
            for r in reasons
            if r
            not in ("no_planned_workout",)
        ]
        if match_score >= 0.8 and len(critical_reasons) <= 1:
            result["detection_confidence"] = "high"
        elif match_score >= 0.5:
            result["detection_confidence"] = "medium"
        else:
            result["detection_confidence"] = "low"

    return result


def build_interval_kpis(
    interval_structure: dict,
    max_hr: Optional[int] = None,
    zones_calibrated: bool = False,
    time_in_zones: Optional[dict] = None,
) -> dict:
    """
    Compute interval-specific coaching KPIs from detected structure.

    Trend-led so the coaching story falls out of the data instead of being
    read off isolated points (#637). Returns dict with:
        rep_pace_consistency_cv: how even the reps were (speed CV %)
        pace: {first_s_per_km, last_s_per_km, fade_s_per_km, direction} across reps
        recovery_floor: {first_pct_max, last_pct_max, delta_pct, trend} -- the HR
            the runner restarts each rep at; a RISING floor is the fatigue tell
        work_rest_ratio: actual work:rest
        total_z4_plus_s: seconds in Z4+Z5 (only if zones calibrated)
    """
    work_segments = interval_structure.get("work_segments", [])
    rest_segments = interval_structure.get("rest_segments", [])
    summary = interval_structure.get("summary", {})

    kpis: Dict[str, Any] = {}

    # Rep pace consistency (how even the reps were).
    kpis["rep_pace_consistency_cv"] = summary.get("work_speed_cv")

    # Pace across reps, in coach units (s/km): where it started, where it ended,
    # and the fade. Positive fade = slower (fading); negative = negative split.
    paces = [w.get("pace_s_per_km") for w in work_segments if w.get("pace_s_per_km")]
    if len(paces) >= 2:
        fade = paces[-1] - paces[0]
        direction = (
            "fading" if fade > _PACE_FADE_DEADBAND_S
            else "negative_split" if fade < -_PACE_FADE_DEADBAND_S
            else "holding"
        )
        kpis["pace"] = {
            "first_s_per_km": paces[0],
            "last_s_per_km": paces[-1],
            "fade_s_per_km": fade,
            "direction": direction,
        }
    else:
        kpis["pace"] = None

    # Recovery floor across the session: the HR the runner restarts each rep at,
    # as % of max. A RISING floor means recovery was getting incomplete -- the
    # clearest fatigue signal in the set, and the one the raw per-rest drop hid.
    floors = [
        r.get("restart_pct_max") for r in rest_segments
        if r.get("restart_pct_max") is not None
    ]
    if len(floors) >= 2:
        delta = floors[-1] - floors[0]
        trend = (
            "rising" if delta >= _FLOOR_TREND_DEADBAND_PCT
            else "falling" if delta <= -_FLOOR_TREND_DEADBAND_PCT
            else "flat"
        )
        kpis["recovery_floor"] = {
            "first_pct_max": floors[0],
            "last_pct_max": floors[-1],
            "delta_pct": delta,
            "trend": trend,
        }
    else:
        kpis["recovery_floor"] = None

    # Work:rest ratio
    kpis["work_rest_ratio"] = summary.get("work_to_rest_ratio")

    # Total time in Z4+ (only meaningful with calibrated zones)
    if zones_calibrated and time_in_zones:
        z4 = time_in_zones.get("Z4", 0) or 0
        z5 = time_in_zones.get("Z5", 0) or 0
        kpis["total_z4_plus_s"] = z4 + z5
    else:
        kpis["total_z4_plus_s"] = None

    return kpis


def _cv_percent(values: list) -> Optional[float]:
    """Coefficient of variation as percentage."""
    if not values or len(values) < 2:
        return None
    arr = np.array(values, dtype=float)
    mean = np.mean(arr)
    if mean == 0:
        return None
    return round(float((np.std(arr, ddof=1) / mean) * 100), 1)
