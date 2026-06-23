"""Consolidated stream view (A2a processed-artifacts layer).

A small, downsampled, index-aligned snapshot of an activity's HR / pace / grade
/ cadence streams, produced during analysis and stored on the DerivedMetric row
so retrieval is cheap (the "do the work on ingestion" principle). The raw
per-sample streams never enter the coach's context; this lean view is the
retrievable middle tier the coach reasons about when an activity is the subject
of an exchange.

This is a pure function. It is re-derived on every analysis (so it self-heals on
re-sync / backfill) and is a convenience VIEW over the raw store, never a source
of truth: it must never override a re-derived DerivedMetric (CONTEXT.md,
"Processed artifacts").

Channel contract of the returned dict (keys always present; a channel absent
from the input is None rather than an array, so the reader sees a predictable
shape):
    n_points       int   downsampled length (== len of every channel array)
    source_n       int   aligned raw length the view was built from
    time_s         [int] bucket-representative time, seconds, monotonic non-decreasing
                   (strictly increasing for the usual integer-second Strava stream;
                   sub-second input at a low point count may round adjacent buckets equal)
    hr             [int|None] | None         bucket-mean heart rate, bpm
    pace_s_per_km  [int|None] | None         derived from velocity, None when stopped
    grade_pct      [float|None] | None       bucket-mean grade, percent
    cadence_spm    [int|None] | None         bucket-mean cadence, steps/min
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.units.cadence import cadence_doubling_factor

# Target resolution. "Tens of points, not thousands" (brief § A2a): ~60 points
# is roughly one point per minute over an hour run, lean enough for context yet
# dense enough to show where HR drifted, pace surged, or the route climbed.
# Resolution (fixed point count vs per-split) is a build-time tuning detail
# (design doc § 7); a fixed cap is the simpler, deterministic choice.
STREAM_VIEW_MAX_POINTS = 60

# Below this speed the runner is effectively stopped (autopause / standing): a
# derived pace would blow up toward infinity, so we report None instead. 0.5 m/s
# is ~33 min/km, slower than any real running or walking pace.
_MIN_MOVING_SPEED_MPS = 0.5

_TIME = "time"
_HR = "heartrate"
_VELOCITY = "velocity_smooth"
_GRADE = "grade_smooth"
_CADENCE = "cadence"


def _bucket_means(values: List[Any], n_buckets: int) -> List[Optional[float]]:
    """Downsample `values` to `n_buckets` contiguous bucket means.

    None entries are ignored within a bucket; a bucket with no real value
    becomes None. Buckets are sized as evenly as possible so the whole series
    is covered (no stride decimation that could skip a peak).
    """
    n = len(values)
    out: List[Optional[float]] = []
    for i in range(n_buckets):
        start = i * n // n_buckets
        end = (i + 1) * n // n_buckets
        if end <= start:
            end = start + 1
        nums = [v for v in values[start:end] if isinstance(v, (int, float))]
        out.append(sum(nums) / len(nums) if nums else None)
    return out


def _round_opt(value: Optional[float], ndigits: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    rounded = round(value, ndigits) if ndigits is not None else round(value)
    return rounded


def build_stream_view(
    streams: Optional[Dict[str, List[Any]]],
    *,
    max_points: int = STREAM_VIEW_MAX_POINTS,
) -> Optional[Dict[str, Any]]:
    """Build the consolidated stream view, or None when there is nothing to show.

    `streams` is the analysis `streams_dict`: {channel_name: [values...]}, with
    channels stored as independent, positionally-aligned arrays.
    """
    if not streams:
        return None

    time = streams.get(_TIME)
    if not time:
        return None

    # The metric channels we care about, in output order.
    present = {
        ch: streams[ch]
        for ch in (_HR, _VELOCITY, _GRADE, _CADENCE)
        if streams.get(ch)
    }
    if not present:
        return None  # time but no metric channel: nothing worth viewing.

    # Align defensively: channel arrays are NOT guaranteed equal length.
    common_len = min(len(time), *(len(v) for v in present.values()))
    if common_len <= 0:
        return None

    n_buckets = min(common_len, max_points)

    time_ds = _bucket_means([t for t in time[:common_len]], n_buckets)
    time_s = [int(round(t)) if t is not None else None for t in time_ds]

    view: Dict[str, Any] = {
        "n_points": n_buckets,
        "source_n": common_len,
        "time_s": time_s,
        "hr": None,
        "pace_s_per_km": None,
        "grade_pct": None,
        "cadence_spm": None,
    }

    if _HR in present:
        hr_ds = _bucket_means(present[_HR][:common_len], n_buckets)
        view["hr"] = [int(_round_opt(v, None)) if v is not None else None for v in hr_ds]

    if _VELOCITY in present:
        vel_ds = _bucket_means(present[_VELOCITY][:common_len], n_buckets)
        view["pace_s_per_km"] = [_velocity_to_pace(v) for v in vel_ds]

    if _GRADE in present:
        grade_ds = _bucket_means(present[_GRADE][:common_len], n_buckets)
        view["grade_pct"] = [_round_opt(v, 1) for v in grade_ds]

    if _CADENCE in present:
        cad_raw = present[_CADENCE][:common_len]
        # Decide the per-leg factor once from the series mean (the shared rule,
        # #442), so a momentary low-cadence dip is never spuriously doubled.
        factor = cadence_doubling_factor(_series_mean(cad_raw))
        cad_ds = _bucket_means(cad_raw, n_buckets)
        view["cadence_spm"] = [
            int(round(v * factor)) if v is not None else None for v in cad_ds
        ]

    return view


def _velocity_to_pace(velocity: Optional[float]) -> Optional[int]:
    if velocity is None or velocity <= _MIN_MOVING_SPEED_MPS:
        return None
    return int(round(1000.0 / velocity))


def _series_mean(values: List[Any]) -> Optional[float]:
    """The mean of the numeric entries in `values`, or None when there are none.
    The representative figure the shared cadence rule judges (units.cadence)."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return sum(nums) / len(nums)
