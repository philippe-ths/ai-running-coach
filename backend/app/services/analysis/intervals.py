"""
Interval session structure detection.

Detects work/rest segments from velocity stream data so the coach can
discuss rep consistency, recovery quality, and work-to-rest ratios.
"""

from typing import Dict, List, Optional

import numpy as np


def detect_intervals(
    streams_dict: Dict[str, List],
    activity_class: str,
) -> Optional[dict]:
    """
    Detect work/rest segments in an interval session.

    Returns None if activity_class is not "Intervals" or data is insufficient.
    Returns a dict with warmup, work_segments, rest_segments, and summary.
    """
    if activity_class != "Intervals":
        return None

    velocity = streams_dict.get("velocity_smooth")
    if not velocity or len(velocity) < 60:
        return None

    vel_arr = np.array(velocity, dtype=float)
    hr_arr = np.array(streams_dict["heartrate"], dtype=float) if "heartrate" in streams_dict else None

    # Smooth velocity with 30s rolling average
    kernel_size = min(30, len(vel_arr))
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(vel_arr, kernel, mode="same")

    # Threshold: midpoint between fast and slow clusters
    active = smoothed[smoothed > 0.5]  # ignore near-zero (stops)
    if len(active) < 60:
        return None

    midpoint = _bimodal_threshold(active)
    if midpoint is None:
        return None

    work_threshold = midpoint * 1.05
    rest_threshold = midpoint * 0.95

    # Label each second: 'work', 'rest', or 'transition'
    labels = np.where(
        smoothed >= work_threshold, 1,      # work
        np.where(smoothed <= rest_threshold, -1, 0)  # rest / transition
    )

    # Extract contiguous segments
    raw_segments = _extract_segments(labels)

    # Filter by minimum durations
    work_segs = [s for s in raw_segments if s["type"] == "work" and s["duration_s"] >= 30]
    rest_segs = [s for s in raw_segments if s["type"] == "rest" and s["duration_s"] >= 15]

    if len(work_segs) < 2:
        return None  # Not enough reps to be a meaningful interval session

    # Detect warmup and cooldown
    first_work_start = work_segs[0]["start"]
    last_work_end = work_segs[-1]["start"] + work_segs[-1]["duration_s"]

    warmup_duration = first_work_start if first_work_start >= 120 else None
    cooldown_duration = (
        (len(vel_arr) - last_work_end)
        if (len(vel_arr) - last_work_end) >= 120
        else None
    )

    # Build detailed work segments
    distance_arr = np.array(streams_dict["distance"], dtype=float) if "distance" in streams_dict else None

    work_details = []
    for idx, seg in enumerate(work_segs):
        s, e = seg["start"], seg["start"] + seg["duration_s"]
        detail = {
            "segment_number": idx + 1,
            "start_time_s": s,
            "duration_s": seg["duration_s"],
            "distance_m": round(float(distance_arr[min(e, len(distance_arr) - 1)] - distance_arr[s]), 1) if distance_arr is not None else None,
            "avg_speed_mps": round(float(np.mean(vel_arr[s:e])), 2),
            "avg_hr": round(float(np.mean(hr_arr[s:e])), 1) if hr_arr is not None else None,
            "peak_hr": round(float(np.max(hr_arr[s:e])), 1) if hr_arr is not None else None,
        }
        work_details.append(detail)

    # Build detailed rest segments (only those between work segments)
    rest_details = []
    for idx, rest in enumerate(rest_segs):
        rs, re_ = rest["start"], rest["start"] + rest["duration_s"]
        # Only include rests that fall between work segments
        if rs < first_work_start or rs >= last_work_end:
            continue
        # Find the preceding work segment's peak HR for recovery calculation
        prev_peak_hr = None
        for w in reversed(work_details):
            if w["start_time_s"] + w["duration_s"] <= rs:
                prev_peak_hr = w["peak_hr"]
                break

        avg_rest_hr = round(float(np.mean(hr_arr[rs:re_])), 1) if hr_arr is not None else None
        rest_details.append({
            "segment_number": len(rest_details) + 1,
            "duration_s": rest["duration_s"],
            "avg_hr": avg_rest_hr,
            "hr_recovery_bpm": (
                round(prev_peak_hr - avg_rest_hr, 1)
                if prev_peak_hr is not None and avg_rest_hr is not None
                else None
            ),
        })

    # Summary statistics
    work_durations = [w["duration_s"] for w in work_details]
    work_speeds = [w["avg_speed_mps"] for w in work_details]
    rest_durations = [r["duration_s"] for r in rest_details]
    hr_recoveries = [r["hr_recovery_bpm"] for r in rest_details if r["hr_recovery_bpm"] is not None]

    total_work = sum(work_durations)
    total_rest = sum(rest_durations) if rest_durations else 0

    work_dur_cv = _cv_percent(work_durations)
    work_speed_cv = _cv_percent(work_speeds)

    summary = {
        "total_work_time_s": total_work,
        "total_rest_time_s": total_rest,
        "work_to_rest_ratio": round(total_work / total_rest, 2) if total_rest > 0 else None,
        "rep_count": len(work_details),
        "avg_work_duration_s": round(np.mean(work_durations)),
        "work_duration_cv": round(work_dur_cv, 1) if work_dur_cv is not None else None,
        "avg_work_speed_mps": round(float(np.mean(work_speeds)), 2),
        "work_speed_cv": round(work_speed_cv, 1) if work_speed_cv is not None else None,
        "avg_rest_duration_s": round(np.mean(rest_durations)) if rest_durations else None,
        "avg_hr_recovery_bpm": round(float(np.mean(hr_recoveries)), 1) if hr_recoveries else None,
        "consistency_score": _consistency_label(work_dur_cv, work_speed_cv),
    }

    return {
        "warmup_duration_s": warmup_duration,
        "cooldown_duration_s": cooldown_duration,
        "work_segments": work_details,
        "rest_segments": rest_details,
        "summary": summary,
    }


def _bimodal_threshold(speeds: np.ndarray) -> Optional[float]:
    """
    Find a threshold that separates fast (work) and slow (rest) speeds.

    Uses a simple iterative approach: start with the mean, then refine by
    computing the mean of values above and below the threshold, and taking
    the midpoint. Converges quickly for bimodal distributions.
    """
    if len(speeds) < 10:
        return None

    threshold = float(np.mean(speeds))

    for _ in range(20):  # converges in a few iterations
        low = speeds[speeds <= threshold]
        high = speeds[speeds > threshold]
        if len(low) == 0 or len(high) == 0:
            return None
        new_threshold = (float(np.mean(low)) + float(np.mean(high))) / 2
        if abs(new_threshold - threshold) < 0.01:
            break
        threshold = new_threshold

    # Verify there's meaningful separation
    low = speeds[speeds <= threshold]
    high = speeds[speeds > threshold]
    if len(low) == 0 or len(high) == 0:
        return None
    low_mean = float(np.mean(low))
    high_mean = float(np.mean(high))
    # Require at least 30% speed difference between clusters
    if high_mean < low_mean * 1.3:
        return None

    return threshold


def _extract_segments(labels: np.ndarray) -> List[dict]:
    """Extract contiguous segments of same label from the labels array."""
    segments = []
    if len(labels) == 0:
        return segments

    current_label = labels[0]
    start = 0

    for i in range(1, len(labels)):
        if labels[i] != current_label:
            label_name = {1: "work", -1: "rest"}.get(int(current_label), "transition")
            segments.append({
                "type": label_name,
                "start": int(start),
                "duration_s": int(i - start),
            })
            current_label = labels[i]
            start = i

    # Final segment
    label_name = {1: "work", -1: "rest"}.get(int(current_label), "transition")
    segments.append({
        "type": label_name,
        "start": int(start),
        "duration_s": int(len(labels) - start),
    })

    return segments


def _cv_percent(values: List[float]) -> Optional[float]:
    """Coefficient of variation as a percentage. Returns None if < 2 values."""
    if len(values) < 2:
        return None
    arr = np.array(values, dtype=float)
    mean = np.mean(arr)
    if mean == 0:
        return None
    return float((np.std(arr, ddof=1) / mean) * 100)


def _consistency_label(dur_cv: Optional[float], speed_cv: Optional[float]) -> str:
    """Derive a consistency label from duration and speed CVs."""
    # Use the worse (higher) of the two CVs
    cvs = [c for c in [dur_cv, speed_cv] if c is not None]
    if not cvs:
        return "unknown"
    worst_cv = max(cvs)
    if worst_cv < 10:
        return "high"
    elif worst_cv < 20:
        return "medium"
    else:
        return "low"
