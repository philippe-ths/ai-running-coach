"""Every measure the chat coach can read off the training record (#1071).

The chat tools used to reach only the fields someone had foreseen a question for.
A runner asked for their weekly zone 2+ time, the app held it on every activity,
and the coach answered "I can't confirm" because no tool returned it. This module
is the fix at the class: ONE declaration per measure, from which the
`get_training_metric` tool, the per-session list and the completeness test all
derive.

Two rules hold here:

  1. The SERVER decides how a measure combines. Summing average heart rate across
     sessions gives a plausible-looking number that means nothing, so the model
     never chooses sum vs average: each measure declares its own combination.
  2. A session with no data for a measure is NOT a zero. Each result counts the
     sessions that had data, so a week where the HR strap stayed home reads as
     incomplete, not as an easy week.

`STORED_FIELDS_EXCLUDED` is the other half of the completeness test
(`test_training_metrics_1071.py`): every stored per-activity field is either read
by a measure here, served by a named tool, or listed there with the reason it is
not. A new stored field fails the build until someone makes that call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.coach import coach_units
from app.services.schedule.norms import zone_seconds
from app.services.units.cadence import normalize_cadence_spm
from app.services.weeks import week_start


# How a measure combines across sessions. Each reads differently to the coach, so
# the result says which one was used in words.
SUM = "sum"
COUNT = "count"
TIME_WEIGHTED_MEAN = "time_weighted_mean"
MEAN = "mean"
MAX = "max"
PACE = "pace"
COUNT_BY = "count_by"

HOW_COMBINED = {
    SUM: "summed across the sessions that recorded it",
    COUNT: "a count of sessions",
    TIME_WEIGHTED_MEAN: "averaged across the sessions that recorded it, weighted by moving time",
    MEAN: "averaged across the sessions that recorded it, one session one vote",
    MAX: "the highest single-session value",
    PACE: "total distance over total moving time, across the sessions that recorded both",
    COUNT_BY: "sessions counted by label",
}


@dataclass(frozen=True)
class Metric:
    key: str
    description: str
    unit: str
    combine: str
    # One session's value, or None when that session has no data for it. For
    # COUNT_BY the value is the label; for PACE it is (distance_m, moving_time_s).
    value: Callable[[Any, int], Any]
    # The stored fields this measure reads (the completeness test's evidence).
    reads: Tuple[str, ...]
    # Runs only unless the coach names another type: pace and cadence across a
    # run and a ride together are not one number.
    default_type: Optional[str] = None
    # Reads the runner's HR zones, so the result carries their calibration.
    uses_zones: bool = False


def _moving_s(fact) -> float:
    return fact.moving_time_s or 0


def _zone_time_s(fact, min_zone: int) -> Optional[float]:
    by_zone = zone_seconds(fact)
    if not by_zone:
        return None
    return sum(s for zone, s in by_zone.items() if zone >= min_zone)


# Cadence in steps per minute means something only on foot: a ride's crank rpm
# doubled by the per-leg rule would read as a running cadence.
FOOT_TYPES = frozenset({"run", "virtualrun", "trailrun", "walk", "hike"})


def _cadence(fact, _z) -> Optional[float]:
    if not fact.avg_cadence:
        return None
    if (fact.user_intent or fact.activity_type or "").lower() not in FOOT_TYPES:
        return None
    return normalize_cadence_spm(fact.user_intent or fact.activity_type, fact.avg_cadence)


def _pace_pair(fact, _z):
    if not fact.distance_m or not fact.moving_time_s:
        return None
    return (fact.distance_m, fact.moving_time_s)


def _flag(name: str):
    def read(fact, _z):
        value = getattr(fact, name, None)
        return None if value is None else (1 if value else 0)
    return read


METRICS: Tuple[Metric, ...] = (
    Metric("sessions", "how many sessions", "sessions", COUNT,
           lambda f, _z: 1, ()),
    Metric("distance_km", "distance", "km", SUM,
           lambda f, _z: (f.distance_m or 0) / 1000, ("distance_m",)),
    Metric("moving_time_h", "moving time", "h", SUM,
           lambda f, _z: (f.moving_time_s or 0) / 3600, ("moving_time_s",)),
    Metric("elapsed_time_h", "elapsed time, stops included", "h", SUM,
           lambda f, _z: (f.elapsed_time_s or 0) / 3600, ("elapsed_time_s",)),
    Metric("elevation_gain_m", "elevation gain", "m", SUM,
           lambda f, _z: f.elev_gain_m or 0, ("elev_gain_m",)),
    Metric("zone_time_h", "time at or above a heart-rate zone (set min_zone; 2 means zone 2+)",
           "h", SUM,
           lambda f, z: None if (s := _zone_time_s(f, z)) is None else s / 3600,
           ("time_in_zones",), uses_zones=True),
    Metric("training_load", "the app's training load (effort score)", "load points", SUM,
           lambda f, _z: f.effort_score, ("effort_score",)),
    Metric("avg_hr_bpm", "average heart rate", "bpm", TIME_WEIGHTED_MEAN,
           lambda f, _z: f.avg_hr or None, ("avg_hr",)),
    Metric("max_hr_bpm", "highest heart rate reached", "bpm", MAX,
           lambda f, _z: f.max_hr or None, ("max_hr",)),
    Metric("avg_pace_per_km", "average pace", "min:sec per km", PACE,
           _pace_pair, ("distance_m", "moving_time_s"), default_type="run"),
    Metric("avg_cadence_spm", "average cadence", "steps per minute", TIME_WEIGHTED_MEAN,
           _cadence, ("avg_cadence",), default_type="run"),
    Metric("hr_drift_pct", "heart-rate drift within a session", "%", MEAN,
           lambda f, _z: f.hr_drift, ("hr_drift",)),
    Metric("pace_variability_pct", "how much pace varied within a session", "% (coefficient of variation)", MEAN,
           lambda f, _z: f.pace_variability, ("pace_variability",)),
    Metric("avg_temperature_c", "air temperature during sessions", "degrees C", MEAN,
           lambda f, _z: f.average_temp, ("average_temp",)),
    Metric("sessions_by_effort", "sessions by effort (recovery, easy, moderate, tempo, hard)",
           "sessions", COUNT_BY,
           lambda f, _z: f.effort, ("effort",)),
    Metric("interval_sessions", "interval sessions", "sessions", SUM,
           lambda f, _z: None if f.structure is None else (1 if f.structure == "intervals" else 0),
           ("structure",)),
    Metric("long_sessions", "long sessions", "sessions", SUM,
           lambda f, _z: None if f.duration_class is None else (1 if f.duration_class == "long" else 0),
           ("duration_class",)),
    Metric("hilly_sessions", "hilly sessions", "sessions", SUM,
           _flag("is_hilly"), ("is_hilly",)),
    Metric("races", "races", "sessions", SUM,
           _flag("is_race"), ("is_race",)),
    Metric("avg_rpe", "how hard sessions felt, as the runner rated them (RPE 1-10)", "RPE", MEAN,
           lambda f, _z: f.rpe, ("rpe",)),
    Metric("highest_pain_score", "the highest pain the runner reported (0-10)", "pain score", MAX,
           lambda f, _z: f.pain_score, ("pain_score",)),
)

BY_KEY: Dict[str, Metric] = {m.key: m for m in METRICS}

# Stored per-activity fields no measure above reads, each with where it IS served
# or why it is not. The completeness test fails on any stored field in neither
# place, so this list is a decision someone made, not a gap nobody saw.
STORED_FIELDS_EXCLUDED: Dict[str, str] = {
    # identity, ownership and bookkeeping
    "id": "identity: served as activity_id",
    "activity_id": "identity: served as activity_id",
    "user_id": "ownership: server-held, never model-visible",
    "strava_activity_id": "an external key with no training meaning",
    "start_date": "served as each session's date",
    "start_date_local": "served as each session's date",
    "local_date": "served as each session's date",
    "activity_type": "served as each session's type and as the type filter",
    "type": "served as each session's type and as the type filter",
    "user_intent": "served through each session's type (the runner's own correction)",
    "average_speed_mps": "the same quantity as pace, which avg_pace_per_km computes from distance and moving time",
    "name": "served per session by list_activities_in_range",
    "raw_summary": "Strava's raw payload; average_temp is the one field read from it",
    "is_deleted": "deleted sessions are never served",
    "block_id": "grouping bookkeeping, not a training measure",
    "coach_notification_sent_at": "notification bookkeeping",
    "opener_notification_sent_at": "notification bookkeeping",
    "receipt_sent_at": "notification bookkeeping",
    "streams_backfilled_at": "sync bookkeeping",
    "created_at": "row bookkeeping",
    "updated_at": "row bookkeeping",
    # served by get_session_detail rather than aggregated
    "interval_structure": "served per session by get_session_detail (rep shape and its source)",
    "confidence": "served per session by get_session_detail (how far the analysis can be trusted)",
    "confidence_reasons": "served per session by get_session_detail",
    "risk_level": "served per session by get_session_detail",
    "risk_score": "served per session by get_session_detail, as its level and reasons",
    "risk_reasons": "served per session by get_session_detail",
    # the runner's check-in
    "pain_location": "served per session by get_session_detail",
    "notes": "served per session by get_session_detail",
    "sleep_quality": "withheld from the coach by the COACH_SLEEP_QUALITY_ENABLED kill switch",
    # structured analyses not yet served to chat (#1072)
    "stops_analysis": "a structured analysis, not a measure; not yet served to chat",
    "efficiency_analysis": "a structured analysis, not a measure; not yet served to chat",
    "workout_match": "a structured analysis, not a measure; not yet served to chat",
    "interval_kpis": "a structured analysis, not a measure; not yet served to chat",
    "training_context": "a structured analysis, not a measure; not yet served to chat",
    "discount_signals": "a structured analysis, not a measure; not yet served to chat",
    "flags": "internal analysis flags, not a measure",
    "stream_view": "the per-second view the session charts draw; too large for chat",
}

# Every other table keyed to an activity, with where it is served or why not. The
# completeness test discovers these from the schema, so a new per-activity table
# fails the build until it is listed here or its fields are read above.
ACTIVITY_TABLES: Dict[str, str] = {
    "activities": "fields",
    "derived_metrics": "fields",
    "check_ins": "fields",
    "activity_streams": "per-second streams; served as splits by get_session_detail",
    "blocks": "grouping of sessions into one outing, not a measure",
    "coach_chat_messages": "the conversation itself, which the coach already holds",
    "coach_reports": "the coach's own written reports, not a training measure",
    "planned_sessions": "the plan, served by get_training_plan",
    "coach_threads": "the conversation itself, which the coach already holds",
}


# --- computing ---------------------------------------------------------------

def _combine(metric: Metric, values: List[Any], weights: List[float]):
    if metric.combine == COUNT:
        return len(values)
    if not values:
        return None
    if metric.combine == SUM:
        return sum(values)
    if metric.combine == MAX:
        return max(values)
    if metric.combine == MEAN:
        return sum(values) / len(values)
    if metric.combine == TIME_WEIGHTED_MEAN:
        total = sum(weights)
        if total <= 0:
            return sum(values) / len(values)
        return sum(v * w for v, w in zip(values, weights)) / total
    if metric.combine == PACE:
        distance = sum(d for d, _ in values)
        seconds = sum(t for _, t in values)
        return coach_units.pace(distance, seconds)
    if metric.combine == COUNT_BY:
        counts: Dict[str, int] = {}
        for label in values:
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    raise ValueError(metric.combine)


def _round(value):
    if isinstance(value, float):
        return round(value, 1)
    return value


def measure(metric: Metric, facts: List[Any], min_zone: int = 2) -> dict:
    """One measure over a set of sessions: the value, how many sessions there were,
    and how many of them recorded it."""
    values, weights = [], []
    for fact in facts:
        value = metric.value(fact, min_zone)
        if value is None:
            continue
        values.append(value)
        weights.append(_moving_s(fact))
    if not facts and metric.combine in (SUM, COUNT):
        value = 0  # no training is a real zero; sessions without data are not
    else:
        value = _combine(metric, values, weights)
    out = {"value": _round(value), "sessions": len(facts)}
    if metric.combine != COUNT and len(values) < len(facts):
        out["sessions_with_data"] = len(values)
    return out


def session_value(metric: Metric, fact: Any, min_zone: int = 2):
    """One session's value for the per-session list, coach-framed."""
    value = metric.value(fact, min_zone)
    if value is None:
        return None
    if metric.combine == PACE:
        return coach_units.pace(*value)
    return _round(value)


def periods(start: date, end_excl: date, group_by: str, week_starts_on: int) -> List[Tuple[date, date]]:
    """The [start, end) periods covering the window, clipped to it."""
    if group_by == "none":
        return [(start, end_excl)]
    out = []
    cursor = start
    while cursor < end_excl:
        if group_by == "week":
            nxt = week_start(cursor, week_starts_on) + timedelta(days=7)
        else:  # month
            nxt = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cursor, min(nxt, end_excl)))
        cursor = nxt
    return out
