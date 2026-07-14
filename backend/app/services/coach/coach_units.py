"""Canonical coach-facing unit formatting (ADR 0026 Slice 4, #680).

One home for every "raw machine unit -> coach-native unit" conversion the coach
LLM sees, so the report context pack (`coach_framing.frame_pack`) and the chat
on-demand tools (`query_tools`) render the same fact the same way and can never
drift. The #637 template: km not metres, min:sec/km not m/s, minute-granularity
durations for whole sessions and second-resolution for interval/zone scale, bpm
with an optional % of max supplement, and no over-precise trailing decimals.

Pure functions, no I/O. Every formatter returns None for a missing input so a
caller can drop the leaf rather than emit a null.
"""

from __future__ import annotations

from typing import Optional


def km(distance_m: Optional[float]) -> Optional[float]:
    """Metres -> kilometres at 1 dp (the pack-wide distance convention).

    Interval-rep distances are deliberately NOT run through this: a 400 m rep is
    coach-native in metres, so callers keep those as metres.
    """
    if not distance_m:
        return None
    return round(distance_m / 1000, 1)


def duration(seconds: Optional[float]) -> Optional[str]:
    """Whole-session / aggregate duration at minute granularity: '30m' / '1h01m'.

    Matches the existing chat-tool convention. Seconds are noise at session scale
    ("you ran 30 minutes", not "30 minutes 34 seconds").
    """
    if not seconds:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def duration_precise(seconds: Optional[float]) -> Optional[str]:
    """Second-resolution duration for interval/zone scale: 'M:SS' / 'H:MM:SS'.

    Used where the seconds carry signal (a 90 s rep vs a 120 s rep) and where a
    minute-granularity render would lie (12 s in Z1 -> '0m'). Hours are always
    explicit so 'M:SS' can never be misread as hours.
    """
    if seconds is None:
        return None
    s = int(round(seconds))
    if s < 0:
        return None
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _fmt_pace(sec_per_km: float) -> str:
    mins = int(sec_per_km // 60)
    secs = int(round(sec_per_km % 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/km"


def pace(distance_m: Optional[float], moving_time_s: Optional[float]) -> Optional[str]:
    """Average pace as 'm:ss/km' from distance + time, or None."""
    if not distance_m or not moving_time_s or distance_m <= 0:
        return None
    return _fmt_pace(moving_time_s / (distance_m / 1000))


def pace_from_speed(mps: Optional[float]) -> Optional[str]:
    """Pace as 'm:ss/km' from a speed in m/s, or None."""
    if not mps or mps <= 0:
        return None
    return _fmt_pace(1000.0 / mps)


def pace_from_sec_per_km(sec_per_km: Optional[float]) -> Optional[str]:
    """Pace as 'm:ss/km' from a value already in seconds/km, or None."""
    if not sec_per_km or sec_per_km <= 0:
        return None
    return _fmt_pace(sec_per_km)


def pct_of_max(hr: Optional[float], max_hr: Optional[float]) -> Optional[int]:
    """HR as a whole % of the runner's max, or None when the max is unknown."""
    if not hr or not max_hr or max_hr <= 0:
        return None
    return round(hr / max_hr * 100)


def hr_bpm(hr: Optional[float], max_hr: Optional[float] = None) -> Optional[str]:
    """'166 bpm (87% max)' when the max is known, else '166 bpm'; None -> None.

    bpm stays the primary hard number (it is the ground truth; % of max only holds
    if the runner's max is right), with % of max a light supplement.
    """
    if hr is None:
        return None
    b = round(hr)
    pct = pct_of_max(hr, max_hr)
    return f"{b} bpm ({pct}% max)" if pct is not None else f"{b} bpm"


def bpm(hr: Optional[float]) -> Optional[int]:
    """Plain rounded bpm for HR leaves that do NOT get the % max supplement
    (per-rep, aggregate) -- the coach places them against the given max itself."""
    return round(hr) if hr is not None else None


def trim(value):
    """Drop a trailing '.0' (101.0 -> 101, 30.0 -> 30); leave real fractionals and
    non-numbers untouched."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
