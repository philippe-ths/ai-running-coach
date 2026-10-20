"""Named windows, type filters and shared formatting helpers for the chat tools.

Split out of `query_tools.py` (#1102); `query_tools` re-imports every name here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Activity, UserProfile
from app.services.coach import coach_units, training_metrics
from app.services.schedule.norms import zone_seconds
from app.services.weeks import MONDAY, resolve_week_start, week_start



# A single list result is bounded so a wide window cannot flood the context. An
# over-cap result says so (`showing` < `count`) rather than truncating silently.
_MAX_LIST_ACTIVITIES = 40
# The per-km splits summary in a session detail is capped for the same reason.
_MAX_SPLIT_ROWS = 30


# --- named relative windows (server-resolved) --------------------------------

@dataclass(frozen=True)
class ResolvedWindow:
    """A named window resolved to a concrete date range. `start` is None for
    all_time. `end` is exclusive (covers through `today`). `range_key` is the
    `build_volume_report` term this window maps to, or None when it does not map
    (calendar windows / all_time), which is what gates the vs-typical read."""
    start: Optional[date]
    end: date  # exclusive
    label: str
    range_key: Optional[str]


# Rolling windows that map cleanly to a `build_volume_report` term (its rolling
# framing IS the trailing-N-days, so the vs-typical read lines up with no drift).
_ROLLING_DAYS_RANGEKEY = {
    "last_7_days": (7, "7D"),
    "last_30_days": (30, "30D"),
    "last_90_days": (90, "3M"),
    "last_180_days": (180, "6M"),
    "last_365_days": (365, "1Y"),
}

# The full window vocabulary the enumeration tool accepts (rolling + calendar +
# all_time). The summary tool accepts only the rolling ones + all_time.
LIST_WINDOWS = tuple(_ROLLING_DAYS_RANGEKEY) + (
    "last_14_days",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_year",
    "all_time",
) + (
    # #1071: whole calendar weeks ending with this one, so a week-by-week read
    # never opens on a clipped week.
    "last_4_weeks",
    "last_8_weeks",
    "last_12_weeks",
)
SUMMARY_WINDOWS = tuple(_ROLLING_DAYS_RANGEKEY) + ("all_time",)


_WHOLE_WEEKS = {"last_4_weeks": 4, "last_8_weeks": 8, "last_12_weeks": 12}


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _owner_week_start(db: Session, owner_user_id) -> int:
    """The owner's chosen week start (0=Monday default, 6=Sunday), for the calendar
    windows resolve_window computes (#676)."""
    return resolve_week_start(
        db.query(UserProfile).filter(UserProfile.user_id == owner_user_id).first()
    )


def resolve_window(
    window: str, today: date, week_starts_on: int = MONDAY
) -> Optional[ResolvedWindow]:
    """Resolve a named window to a concrete date range as of `today`, or None if the
    name is unknown. Rolling windows end today inclusive; calendar windows follow the
    runner's local calendar; all_time has no start. `week_starts_on` (0=Monday default,
    6=Sunday) sets the boundary for the this_week/last_week windows (#676)."""
    end_excl = today + timedelta(days=1)  # inclusive of today

    if window in _ROLLING_DAYS_RANGEKEY:
        days, range_key = _ROLLING_DAYS_RANGEKEY[window]
        return ResolvedWindow(
            start=end_excl - timedelta(days=days),
            end=end_excl,
            label=f"the last {days} days ({(end_excl - timedelta(days=days)).isoformat()} to {today.isoformat()})",
            range_key=range_key,
        )
    if window == "last_14_days":
        start = end_excl - timedelta(days=14)
        return ResolvedWindow(start, end_excl, f"the last 14 days ({start.isoformat()} to {today.isoformat()})", None)
    if window == "this_week":
        start = week_start(today, week_starts_on)
        return ResolvedWindow(start, end_excl, f"this week ({start.isoformat()} to {today.isoformat()})", None)
    if window == "last_week":
        this_start = week_start(today, week_starts_on)
        start = this_start - timedelta(days=7)
        return ResolvedWindow(start, this_start, f"last week ({start.isoformat()} to {(this_start - timedelta(days=1)).isoformat()})", None)
    if window in _WHOLE_WEEKS:
        weeks = _WHOLE_WEEKS[window]
        start = week_start(today, week_starts_on) - timedelta(days=7 * (weeks - 1))
        return ResolvedWindow(
            start, end_excl,
            f"the last {weeks} calendar weeks, this one included ({start.isoformat()} to {today.isoformat()})",
            None,
        )
    if window == "this_month":
        start = _first_of_month(today)
        return ResolvedWindow(start, end_excl, f"this month ({start.isoformat()} to {today.isoformat()})", None)
    if window == "last_month":
        first_this = _first_of_month(today)
        start = _first_of_month(first_this - timedelta(days=1))
        return ResolvedWindow(start, first_this, f"last month ({start.isoformat()} to {(first_this - timedelta(days=1)).isoformat()})", None)
    if window == "this_year":
        start = date(today.year, 1, 1)
        return ResolvedWindow(start, end_excl, f"this year ({start.isoformat()} to {today.isoformat()})", None)
    if window == "all_time":
        return ResolvedWindow(None, end_excl, "all of the runner's recorded history", None)
    return None


# --- semantic type filter ----------------------------------------------------

# The model picks a coarse, coach-friendly modality; the server maps it to the
# concrete Strava activity types, so the model never has to know the exact type
# strings. An unrecognised value falls through to a case-insensitive exact match,
# and "all"/None means no filter (cross-training stays visible by default).
_TYPE_FILTER: Dict[str, List[str]] = {
    "run": ["Run", "VirtualRun", "TrailRun"],
    "ride": ["Ride", "VirtualRide", "MountainBikeRide", "GravelRide"],
    "strength": ["WeightTraining", "Workout", "Crossfit"],
    "swim": ["Swim"],
    "walk": ["Walk", "Hike"],
}


def _resolve_type_filter(type_filter: Optional[str], db: Session, owner_user_id) -> Optional[List[str]]:
    """Map a coarse modality to concrete Strava types, or None for no filter."""
    if not type_filter or type_filter == "all":
        return None
    key = type_filter.strip().lower()
    if key in _TYPE_FILTER:
        return _TYPE_FILTER[key]
    # Unknown value: match the exact type string (case-insensitive) so an unusual
    # activity type the runner names still works; empty match yields no rows.
    return [type_filter]


# --- coach-framing helpers ---------------------------------------------------

# The coach-framing conventions live in the shared `coach_units` module (ADR 0026
# Slice 4, #680) so these tools and the report context pack (`coach_framing`) render
# every fact identically. These thin wrappers keep the local call sites readable.
def _km(distance_m: Optional[float]) -> Optional[float]:
    return coach_units.km(distance_m)


def _duration(seconds: Optional[float]) -> Optional[str]:
    return coach_units.duration(seconds)


def _pace(distance_m: Optional[float], moving_time_s: Optional[float]) -> Optional[str]:
    """Average pace as 'm:ss/km', or None when it cannot be computed."""
    return coach_units.pace(distance_m, moving_time_s)


def _as_uuid(value) -> Optional[uuid.UUID]:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _records_begin(db: Session, owner_user_id) -> Optional[date]:
    """The earliest activity the app holds for this runner, or None if they have none."""
    earliest = (
        db.query(func.min(Activity.start_date))
        .filter(Activity.user_id == owner_user_id, Activity.is_deleted == False)  # noqa: E712
        .scalar()
    )
    return earliest.date() if earliest else None


def _coverage(db: Session, owner_user_id, resolved: ResolvedWindow) -> dict:
    """Coverage FACTS for the window: when the app's recorded history starts, and — when
    the requested window reaches back before that — a plain note saying so. This is
    substrate, not instruction: the coach can't tell "you didn't train then" from "we
    have no data then", so it used to over-claim ("a new peak for the year") from a
    partial record. Handed the boundary as a fact, its own judgment hedges correctly."""
    begin = _records_begin(db, owner_user_id)
    out: dict = {"records_begin": begin.isoformat() if begin else None}
    if begin is not None and (resolved.start is None or resolved.start < begin):
        out["coverage_note"] = (
            f"Your recorded history in the app begins {begin.isoformat()}; "
            f"this does not include anything before then."
        )
    return out


# --- tool executions (all owner-scoped) --------------------------------------

# Measures the list entry already carries in its own framing (or as a label).
_LISTED_ELSEWHERE = {
    "sessions", "distance_km", "moving_time_h", "avg_pace_per_km",
    "sessions_by_effort", "interval_sessions", "long_sessions", "zone_time_h",
}


# A measure named for its aggregate reads oddly on one session ("avg_rpe": 7).
_PER_SESSION_NAME = {"avg_rpe": "rpe", "highest_pain_score": "pain_score"}


def _session_measures(fact) -> dict:
    """Every registry measure one session recorded (#1071), so no stored figure is
    out of the coach's reach. Absent keys mean the session did not record it."""
    out: dict = {}
    for spec in training_metrics.METRICS:
        if spec.key in _LISTED_ELSEWHERE:
            continue
        value = training_metrics.session_value(spec, fact)
        if value is None:
            continue
        if spec.key in ("hilly_sessions", "races"):
            if value:
                out["hilly" if spec.key == "hilly_sessions" else "race"] = True
            continue
        out[_PER_SESSION_NAME.get(spec.key, spec.key)] = value
    by_zone = zone_seconds(fact)
    if by_zone:
        out["minutes_by_hr_zone"] = {f"Z{z}": round(sec / 60) for z, sec in sorted(by_zone.items())}
    return out
