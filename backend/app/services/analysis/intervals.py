"""
Interval session structure detection.

Two sources, same output contract:

  - `detect_intervals_from_laps` reads the runner's recorded Strava laps
    (their own lap-button marks) and treats them as the authoritative
    interval structure. This is preferred when present: the lap boundaries
    are ground truth, so reps, warmup, and recovery are exact rather than
    re-segmented (#170).
  - `detect_intervals` infers work/rest segments from the velocity stream by
    a smoothing + bimodal-threshold heuristic. This is the fallback used when
    no usable recorded laps are available.

Both return the same `interval_structure` dict so every downstream consumer
(workout matching, KPIs, confidence, the coach pack) is source-agnostic.
"""

from typing import Dict, List, Optional

import numpy as np

# Minimum fast/slow cluster separation for a speed split to be a real
# work/rest pattern rather than noise (fast cluster mean >= 1.3x slow).
# Shared by the stream heuristic and the lap-based splitter.
_MIN_CLUSTER_SEPARATION = 1.3


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

    return {
        "warmup_duration_s": warmup_duration,
        "cooldown_duration_s": cooldown_duration,
        "work_segments": work_details,
        "rest_segments": rest_details,
        "summary": _summarize(work_details, rest_details),
    }


def _summarize(work_details: List[dict], rest_details: List[dict]) -> dict:
    """Build the interval summary block from work/rest segment lists.

    Shared by the stream heuristic and the lap-based detector so both sources
    produce the identical summary contract.
    """
    work_durations = [w["duration_s"] for w in work_details]
    work_speeds = [w["avg_speed_mps"] for w in work_details]
    rest_durations = [r["duration_s"] for r in rest_details]
    hr_recoveries = [r["hr_recovery_bpm"] for r in rest_details if r["hr_recovery_bpm"] is not None]

    total_work = sum(work_durations)
    total_rest = sum(rest_durations) if rest_durations else 0

    work_dur_cv = _cv_percent(work_durations)
    work_speed_cv = _cv_percent(work_speeds)

    return {
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
    # Require meaningful speed difference between clusters
    if high_mean < low_mean * _MIN_CLUSTER_SEPARATION:
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


# ---------------------------------------------------------------------------
# Lap-based interval structure (#170): use the runner's recorded laps as the
# authoritative segmentation instead of re-deriving it from the velocity stream.
# ---------------------------------------------------------------------------

# Need at least two work laps plus one slow lap to read a work/rest pattern.
_MIN_LAPS_FOR_PATTERN = 3


def _lap_speed(lap: dict) -> Optional[float]:
    """Average speed of a lap (m/s), from the recorded field or distance/time."""
    speed = lap.get("average_speed")
    if speed is not None and speed > 0:
        return float(speed)
    distance = lap.get("distance")
    elapsed = lap.get("elapsed_time") or lap.get("moving_time")
    if distance and elapsed and elapsed > 0:
        return float(distance) / float(elapsed)
    return None


def _split_laps_work_rest(speeds: List[float]) -> Optional[List[bool]]:
    """Classify each lap as work (fast) or rest (slow) by speed.

    Splits the laps at the largest gap in their sorted speeds and accepts the
    split only when the fast cluster is clearly faster than the slow cluster
    (>= `_MIN_CLUSTER_SEPARATION`) and there are at least two work laps and one
    slow lap. Returns a per-lap work mask, or None when the laps show no
    work/rest pattern (e.g. uniform auto-distance laps, a steady run).
    """
    n = len(speeds)
    if n < _MIN_LAPS_FOR_PATTERN:
        return None

    ordered = sorted(speeds)
    best_gap = 0.0
    split_at: Optional[int] = None
    for i in range(1, n):
        gap = ordered[i] - ordered[i - 1]
        if gap > best_gap:
            best_gap = gap
            split_at = i
    if split_at is None:
        return None

    slow, fast = ordered[:split_at], ordered[split_at:]
    if len(fast) < 2 or len(slow) < 1:
        return None

    slow_mean = sum(slow) / len(slow)
    fast_mean = sum(fast) / len(fast)
    if slow_mean <= 0 or fast_mean < slow_mean * _MIN_CLUSTER_SEPARATION:
        return None

    threshold = ordered[split_at]  # first speed in the fast cluster
    return [s >= threshold for s in speeds]


def detect_intervals_from_laps(raw_summary: Optional[dict]) -> Optional[dict]:
    """Build interval structure from the runner's recorded Strava laps.

    Returns the same dict contract as `detect_intervals` (plus a
    ``source="recorded_laps"`` marker) when the laps reveal a work/rest
    pattern, otherwise None so the caller can fall back to stream detection.

    The recorded laps are the runner's own segmentation, so unlike the stream
    heuristic there is no smoothing, no minimum-duration floor, and no 120s
    warmup floor: a short recorded warmup or short reps are reported as marked.

    Known limitation: a warmup/cooldown lap paced close to rep pace lands in
    the work cluster and is counted as a rep (an easy-paced warmup/cooldown is
    detected correctly). Separating a brisk warmup from a slow rep by lap
    stats alone is under-determined: every heuristic tried (speed/HR ratio
    tests against interior reps) misfiled some legitimate session shape (a
    faded final rep, a long tempo rep, a cut-down progression, a pyramid), so
    the laps are reported as the runner marked them rather than second-guessed.
    """
    if not raw_summary:
        return None
    laps = raw_summary.get("laps")
    if not isinstance(laps, list) or len(laps) < _MIN_LAPS_FOR_PATTERN:
        return None

    speeds = [_lap_speed(lap) for lap in laps]
    if any(s is None or s <= 0 for s in speeds):
        return None
    durations = [int(lap.get("elapsed_time") or 0) for lap in laps]

    work_mask = _split_laps_work_rest([s for s in speeds])  # type: ignore[arg-type]
    if work_mask is None:
        return None

    work_indices = [i for i, is_work in enumerate(work_mask) if is_work]
    if len(work_indices) < 2:
        return None
    first_work, last_work = work_indices[0], work_indices[-1]
    work_set = set(work_indices)

    # Require at least one recovery (non-work) lap BETWEEN reps. A contiguous
    # block of fast laps with no interleaved recovery is a fast finish or a
    # progression run, not an interval session -> fall back to stream detection.
    if not any(i not in work_set for i in range(first_work + 1, last_work)):
        return None

    # Start offset (seconds from activity start) for each lap.
    starts: List[int] = []
    acc = 0
    for d in durations:
        starts.append(acc)
        acc += d

    work_details = []
    for seg_num, i in enumerate(work_indices, start=1):
        lap = laps[i]
        avg_hr = lap.get("average_heartrate")
        peak_hr = lap.get("max_heartrate")
        distance = lap.get("distance")
        work_details.append({
            "segment_number": seg_num,
            "start_time_s": starts[i],
            "duration_s": durations[i],
            "distance_m": round(float(distance), 1) if distance is not None else None,
            "avg_speed_mps": round(float(speeds[i]), 2),
            "avg_hr": round(float(avg_hr), 1) if avg_hr is not None else None,
            "peak_hr": round(float(peak_hr), 1) if peak_hr is not None else None,
        })

    # Rest segments: recovery laps that fall between the first and last work lap.
    rest_details = []
    for i in range(first_work + 1, last_work):
        if i in work_set:
            continue
        lap = laps[i]
        avg_hr = lap.get("average_heartrate")
        avg_rest_hr = round(float(avg_hr), 1) if avg_hr is not None else None
        prev_peak_hr = None
        for j in range(i - 1, -1, -1):
            if j in work_set:
                prev_peak_hr = laps[j].get("max_heartrate")
                break
        rest_details.append({
            "segment_number": len(rest_details) + 1,
            "duration_s": durations[i],
            "avg_hr": avg_rest_hr,
            "hr_recovery_bpm": (
                round(float(prev_peak_hr) - avg_rest_hr, 1)
                if prev_peak_hr is not None and avg_rest_hr is not None
                else None
            ),
        })

    warmup_duration = (
        sum(durations[i] for i in range(first_work)) if first_work > 0 else None
    )
    cooldown_duration = (
        sum(durations[i] for i in range(last_work + 1, len(laps)))
        if last_work < len(laps) - 1
        else None
    )

    return {
        "warmup_duration_s": warmup_duration,
        "cooldown_duration_s": cooldown_duration,
        "work_segments": work_details,
        "rest_segments": rest_details,
        "summary": _summarize(work_details, rest_details),
        "source": "recorded_laps",
    }
