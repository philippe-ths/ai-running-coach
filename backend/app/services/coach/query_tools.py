"""On-demand coach-chat data tools (#648).

The report path ships the coach a frozen context pack covering fixed recent
windows. In CHAT the runner asks about training data OLDER than those windows,
FRESHER than the snapshot, or about one specific past session — so the coach used
to ask the runner for data we already store ("Can't you see from the data?", the
2026-07-09 review, finding #1). These are the on-demand read tools that let the
chat coach FETCH that data instead of asking for it.

Three narrow, purpose-built tools returning coach-framed derived views (pace not
m/s, effort labels, totals not raw rows), never a general query surface — a small
sharp set the model reaches for confidently rather than a query language it must
compose (KB: tool-deference, complex-mcp):
  - list_activities_in_range: a bounded per-session enumeration over a window
  - get_session_detail:       one named session's deep detail (by activity_id)
  - get_training_summary:      a precomputed all-types aggregate over a window

Two invariants hold across all three, and the tests pin both:
  1. Owner scoping is SERVER-HELD. Every query filters on a `user_id` resolved
     from the chatted activity, never from the model. A model-supplied activity_id
     can only narrow WITHIN the owner's own data; a cross-user id returns empty.
  2. Time is SERVER-RESOLVED. The model picks a NAMED relative window; the server
     resolves it to a concrete date range and ECHOES that range back, so the coach
     never does calendar math (KB: temporal-reasoning-in-llms — models are good at
     temporal language, poor at temporal computation).

The tools re-derive coach-framed views from the same pure builders the pack and
Trends page use (`_query_activity_facts`, `build_volume_report`, `calculate_splits`),
so chat and the rest of the product share one computation. This module is
deliberately NOT in `retrieval.py`: that seam's contract is "retrieve stored
artifacts, never re-derive"; these tools derive.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Activity
from app.models.derived_metric import DerivedMetric
from app.services.coach import coach_units
from app.services.analysis.splits import calculate_splits
from app.services.coach.recent_training import _interval_shape
from app.services.coach.volume import build_volume_report
from app.services.trends import _query_activity_facts

logger = logging.getLogger(__name__)

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
)
SUMMARY_WINDOWS = tuple(_ROLLING_DAYS_RANGEKEY) + ("all_time",)


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def resolve_window(window: str, today: date) -> Optional[ResolvedWindow]:
    """Resolve a named window to a concrete date range as of `today`, or None if the
    name is unknown. Rolling windows end today inclusive; calendar windows follow the
    runner's local calendar; all_time has no start."""
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
        start = today - timedelta(days=today.weekday())  # Monday
        return ResolvedWindow(start, end_excl, f"this week ({start.isoformat()} to {today.isoformat()})", None)
    if window == "last_week":
        this_mon = today - timedelta(days=today.weekday())
        start = this_mon - timedelta(days=7)
        return ResolvedWindow(start, this_mon, f"last week ({start.isoformat()} to {(this_mon - timedelta(days=1)).isoformat()})", None)
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

def list_activities_in_range(
    db: Session, owner_user_id, *, window: str, type_filter: Optional[str], today: date
) -> dict:
    """A bounded, newest-first, coach-framed enumeration of the owner's sessions in a
    window. Each entry carries the per-run distance/pace/effort the frozen pack omits,
    plus the #650 shape markers, and the `activity_id` the coach passes to
    get_session_detail for depth."""
    resolved = resolve_window(window, today)
    if resolved is None:
        return {"error": "unknown_window", "window": window, "allowed": list(LIST_WINDOWS)}

    types = _resolve_type_filter(type_filter, db, owner_user_id)
    facts = _query_activity_facts(
        db, resolved.start, resolved.end, types=types,
        user_id=owner_user_id, include_session_shape=True,
    )
    # Newest first.
    facts = sorted(facts, key=lambda f: f.local_date, reverse=True)
    total = len(facts)
    shown = facts[:_MAX_LIST_ACTIVITIES]

    activities = []
    for f in shown:
        activities.append({
            "activity_id": str(f.activity_id),
            "date": f.local_date.isoformat(),
            "weekday": f.local_date.strftime("%a"),
            "type": f.activity_type,
            "distance_km": _km(f.distance_m),
            "duration": _duration(f.moving_time_s),
            "pace_per_km": _pace(f.distance_m, f.moving_time_s),
            "effort": f.effort,  # HR-derived intensity label, may be None
            "structure": f.structure,
            "interval_shape": _interval_shape(f.interval_structure),
            "long_run": True if f.duration_class == "long" else None,
        })

    return {
        "window": {
            "label": resolved.label,
            "type_filter": type_filter or "all",
            **_coverage(db, owner_user_id, resolved),
        },
        "count": total,
        "showing": len(activities),
        "activities": activities,
    }


def get_session_detail(db: Session, owner_user_id, *, activity_id: str, today: date) -> dict:
    """One named session's deep, coach-framed detail — the metrics a thread turns on
    (cadence, HR drift, interval structure WITH its source so the coach never suggests
    the lap button on a recorded-laps session) plus a capped per-km splits summary.
    Owner-scoped: a cross-user or unknown id returns not_found."""
    aid = _as_uuid(activity_id)
    if aid is None:
        return {"error": "not_found", "activity_id": activity_id}

    activity = (
        db.query(Activity)
        .options(joinedload(Activity.streams), joinedload(Activity.metrics))
        .filter(
            Activity.id == aid,
            Activity.user_id == owner_user_id,  # server-held owner predicate
            Activity.is_deleted == False,  # noqa: E712
        )
        .first()
    )
    if activity is None:
        return {"error": "not_found", "activity_id": activity_id}

    m: Optional[DerivedMetric] = activity.metrics
    local_day = activity.local_start.date()

    interval = None
    if m and isinstance(m.interval_structure, dict):
        istruct = m.interval_structure
        work = istruct.get("work_segments") or []
        interval = {
            "shape": _interval_shape(istruct),
            "source": istruct.get("source"),  # recorded_laps vs stream-detected
            "rep_count": len(work) if work else istruct.get("rep_count"),
            "detection_confidence": istruct.get("detection_confidence") or istruct.get("confidence"),
        }

    effective_type = activity.user_intent or activity.type
    splits_raw = calculate_splits(activity.streams or [], activity_type=effective_type)
    splits_summary = []
    for s in splits_raw[:_MAX_SPLIT_ROWS]:
        pace = s.get("pace")
        splits_summary.append({
            "split": s.get("split"),
            "pace_per_km": _pace(s.get("distance"), s.get("elapsed_time")) if pace is None else _fmt_seconds_per_km(pace),
            "avg_hr": round(s["avg_hr"]) if s.get("avg_hr") else None,
            "avg_cadence_spm": round(s["avg_cadence"]) if s.get("avg_cadence") else None,
        })

    return {
        "activity_id": str(activity.id),
        "date": local_day.isoformat(),
        "weekday": local_day.strftime("%a"),
        "type": activity.type,
        "distance_km": _km(activity.distance_m),
        "duration": _duration(activity.moving_time_s),
        "avg_pace_per_km": _pace(activity.distance_m, activity.moving_time_s),
        "effort": m.effort if m else None,
        "effort_score": round(m.effort_score, 1) if m and m.effort_score is not None else None,
        "avg_hr": coach_units.bpm(activity.avg_hr),
        "avg_cadence_spm": round(activity.avg_cadence) if activity.avg_cadence else None,
        "hr_drift_pct": round(m.hr_drift, 1) if m and m.hr_drift is not None else None,
        "structure": m.structure if m else None,
        "interval": interval,
        "splits": splits_summary,
    }


def _fmt_seconds_per_km(sec_per_km: Optional[float]) -> Optional[str]:
    return coach_units.pace_from_sec_per_km(sec_per_km)


def get_training_summary(
    db: Session, owner_user_id, *, window: str, type_filter: Optional[str], today: date
) -> dict:
    """A precomputed all-types aggregate over a window: totals + a by-type breakdown
    (cross-training balance) + a vs-typical read, so the coach cites a computed answer
    rather than summing rows itself. vs-typical reuses `build_volume_report`'s rolling
    framing (canonical norm, no drift) and is present only for a rolling window with no
    type filter (the norm is holistic)."""
    resolved = resolve_window(window, today)
    if resolved is None or window not in SUMMARY_WINDOWS:
        return {"error": "unknown_window", "window": window, "allowed": list(SUMMARY_WINDOWS)}

    types = _resolve_type_filter(type_filter, db, owner_user_id)
    window_facts = _query_activity_facts(
        db, resolved.start, resolved.end, types=types, user_id=owner_user_id,
    )

    totals = _totals(window_facts)
    by_type = _by_type(window_facts)

    vs_typical = None
    if resolved.range_key and not types:
        # Canonical norm from the shared Trends builder: pass the owner's full history
        # so it can establish the baseline, ask for this window's rolling framing.
        all_facts = _query_activity_facts(db, None, resolved.end, user_id=owner_user_id)
        try:
            report = build_volume_report(all_facts, today, resolved.range_key)
            vs_typical = _vs_typical(report)
        except Exception as exc:  # a norm failure must never fail the whole tool
            logger.warning("training_summary vs_typical failed: %s", exc)

    return {
        "window": {
            "label": resolved.label,
            "type_filter": type_filter or "all",
            **_coverage(db, owner_user_id, resolved),
        },
        "totals": totals,
        "by_type": by_type,
        "vs_typical": vs_typical,
    }


def _totals(facts: List[Any]) -> dict:
    return {
        "sessions": len(facts),
        "distance_km": round(sum(f.distance_m or 0 for f in facts) / 1000, 1),
        "moving_time": _duration(sum(f.moving_time_s or 0 for f in facts)),
        "effort_score": round(sum(f.effort_score or 0 for f in facts), 1),
    }


def _by_type(facts: List[Any]) -> List[dict]:
    groups: Dict[str, dict] = {}
    for f in facts:
        t = f.activity_type or "Unknown"
        g = groups.setdefault(t, {"sessions": 0, "distance_m": 0.0, "moving_time_s": 0.0})
        g["sessions"] += 1
        g["distance_m"] += f.distance_m or 0
        g["moving_time_s"] += f.moving_time_s or 0
    out = []
    for t in sorted(groups, key=lambda k: (-groups[k]["sessions"], k)):
        g = groups[t]
        out.append({
            "type": t,
            "sessions": g["sessions"],
            "distance_km": round(g["distance_m"] / 1000, 1),
            "moving_time": _duration(g["moving_time_s"]),
        })
    return out


def _vs_typical(report) -> dict:
    """Project `build_volume_report`'s rolling framing into a compact vs-typical read
    (direction + pct per metric), or a has_baseline flag when history is too thin."""
    rolling = report.rolling
    if not report.has_baseline:
        return {"has_baseline": False, "baseline_label": report.baseline_label}
    metrics = {}
    for m in rolling.metrics:
        metrics[m.metric] = {"direction": m.direction, "pct_vs_norm": m.pct_vs_norm}
    return {"has_baseline": True, "baseline_label": report.baseline_label, "metrics": metrics}


# --- tool schemas + dispatch -------------------------------------------------

# The status label shown in chat while each tool runs (the ephemeral "fetching"
# affordance, #648). Coach-framed, so it reads as a competent coach checking the
# record rather than a spinner.
TOOL_STATUS_LABELS = {
    "list_activities_in_range": "Checking your training history…",
    "get_session_detail": "Pulling up that session…",
    "get_training_summary": "Tallying your recent training…",
}


CHAT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_activities_in_range",
        "description": (
            "List the runner's own sessions (all activity types, newest first) over a "
            "named time window, each with its per-session distance, pace, effort, and "
            "shape. Use this to answer questions about training history the analysis "
            "above does not already cover — how far a past run was, when the runner last "
            "did a workout of some kind, how their long runs have progressed, how much "
            "cross-training they have done. Pick the window whose name matches how the "
            "runner spoke; never compute dates yourself. Returns each session's "
            "activity_id — pass it to get_session_detail when you need that session's "
            "depth."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "enum": list(LIST_WINDOWS),
                    "description": "The named time window to list, chosen to match the runner's phrasing.",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["all", "run", "ride", "strength", "swim", "walk"],
                    "description": "Optional modality filter; omit or 'all' to include cross-training.",
                },
            },
            "required": ["window"],
        },
    },
    {
        "name": "get_session_detail",
        "description": (
            "Fetch one past session's full detail by its activity_id (obtained from "
            "list_activities_in_range): pace, HR, cadence, HR drift, and its interval "
            "structure INCLUDING whether the reps came from the runner's own recorded "
            "laps, plus a per-km splits summary. Use this to settle a question about a "
            "specific earlier session — for example whether a cadence held, or how an "
            "interval workout was actually run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "string",
                    "description": "The activity_id of the session, from a list_activities_in_range result.",
                },
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "get_training_summary",
        "description": (
            "Get a computed training total over a named window: session count, distance, "
            "time, and load, a by-type breakdown (running vs strength vs riding), and a "
            "vs-typical read of whether the runner is training more or less than usual. "
            "Use this for 'how much have I …' and 'am I doing more/less than usual' "
            "questions instead of adding up individual sessions yourself. Pick the window "
            "by name; never compute dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "enum": list(SUMMARY_WINDOWS),
                    "description": "The named time window to total, chosen to match the runner's phrasing.",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["all", "run", "ride", "strength", "swim", "walk"],
                    "description": "Optional modality filter; omit or 'all' for the whole training picture.",
                },
            },
            "required": ["window"],
        },
    },
]


def execute_chat_tool(
    db: Session, owner_user_id, name: str, tool_input: Dict[str, Any], today: Optional[date] = None
) -> dict:
    """Dispatch one owner-scoped tool call. Never raises for a data problem — a bad
    input or a query error returns a structured `{"error": ...}` the coach reasons
    over, so a single failed fetch never crashes the chat turn (#648, Q8)."""
    today = today or date.today()
    tool_input = tool_input or {}
    try:
        if name == "list_activities_in_range":
            return list_activities_in_range(
                db, owner_user_id,
                window=tool_input.get("window", ""),
                type_filter=tool_input.get("type_filter"),
                today=today,
            )
        if name == "get_session_detail":
            return get_session_detail(
                db, owner_user_id,
                activity_id=str(tool_input.get("activity_id", "")),
                today=today,
            )
        if name == "get_training_summary":
            return get_training_summary(
                db, owner_user_id,
                window=tool_input.get("window", ""),
                type_filter=tool_input.get("type_filter"),
                today=today,
            )
        return {"error": "unknown_tool", "tool": name}
    except Exception as exc:  # graceful degrade — the coach answers from what it has
        logger.warning("chat tool %s failed: %s", name, exc)
        return {"error": "fetch_failed", "tool": name}
