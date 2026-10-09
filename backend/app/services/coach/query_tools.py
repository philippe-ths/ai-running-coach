"""On-demand coach-chat data tools (#648).

The report path ships the coach a frozen context pack covering fixed recent
windows. In CHAT the runner asks about training data OLDER than those windows,
FRESHER than the snapshot, or about one specific past session — so the coach used
to ask the runner for data we already store ("Can't you see from the data?", the
2026-07-09 review, finding #1). These are the on-demand read tools that let the
chat coach FETCH that data instead of asking for it.

The model chooses; the server computes (#1071, ADR 0033). Every tool takes a
few fixed options (a named window, a modality, a metric from a fixed list) and
returns coach-framed derived views (pace not m/s, effort labels, totals not raw
rows), never a query the model composes: models are fluent in temporal and
statistical language and unreliable at the arithmetic behind it (KB:
tool-deference, complex-mcp). That rule limits how the coach ASKS, not what it
can REACH. Anything the app stores per activity is reachable through
`get_training_metric` and the per-session tools, and
`test_training_metrics_1071.py` fails the build when a stored field is neither
served nor excluded with a reason in `training_metrics`.

  - list_activities_in_range: a bounded per-session enumeration over a window
  - get_session_detail:       one named session's deep detail (by activity_id)
  - get_training_summary:     a precomputed all-types aggregate over a window
  - get_training_metric:      any stored measure over a window, whole or per week/month

Two invariants hold across all of them, and the tests pin both:
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

from app.services.schedule import goals
from app.models import Activity, UserProfile
from app.models.derived_metric import DerivedMetric
from app.services.coach import coach_units, training_metrics
from app.services.analysis.splits import calculate_splits
from app.services.units.cadence import normalize_cadence_spm
from app.services.coach.recent_training import _interval_shape
from app.services.coach.volume import build_volume_report
from app.services.activity_facts import query_facts as _query_activity_facts
from app.services.schedule.norms import zone_seconds
from app.services.weeks import MONDAY, resolve_week_start, week_start

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


def list_activities_in_range(
    db: Session, owner_user_id, *, window: str, type_filter: Optional[str], today: date
) -> dict:
    """A bounded, newest-first, coach-framed enumeration of the owner's sessions in a
    window. Each entry carries the per-run distance/pace/effort the frozen pack omits,
    plus the #650 shape markers, and the `activity_id` the coach passes to
    get_session_detail for depth."""
    resolved = resolve_window(window, today, _owner_week_start(db, owner_user_id))
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
    _attach_check_ins(db, owner_user_id, shown)

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
            "name": f.name,
            **_session_measures(f),
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
        # Same shared per-leg -> spm rule (and the same effective type) the splits above
        # and the coach pack use, so one payload reports one cadence unit (#934).
        "avg_cadence_spm": (
            round(normalize_cadence_spm(effective_type, activity.avg_cadence))
            if activity.avg_cadence
            else None
        ),
        "hr_drift_pct": round(m.hr_drift, 1) if m and m.hr_drift is not None else None,
        "structure": m.structure if m else None,
        "interval": interval,
        "splits": splits_summary,
        # #1071: the rest of what the app stores for this session.
        **_detail_extras(activity, m),
    }


def _detail_extras(activity, m: Optional[DerivedMetric]) -> dict:
    from app.services.activity_facts import ActivityFact

    fact = ActivityFact(activity)
    # The detail already states cadence, drift, HR and load in its own keys.
    already = {"avg_cadence_spm", "hr_drift_pct", "avg_hr_bpm", "training_load"}
    out = {"name": activity.name}
    out.update({k: v for k, v in _session_measures(fact).items() if k not in already})
    check_in = activity.check_in
    if check_in is not None:
        if check_in.pain_location:
            out["pain_location"] = check_in.pain_location
        if check_in.notes:
            out["runner_notes"] = check_in.notes
    if m is not None:
        out["analysis_confidence"] = m.confidence
        if m.confidence_reasons:
            out["analysis_confidence_reasons"] = list(m.confidence_reasons)
        if m.risk_level:
            out["session_risk"] = {"level": m.risk_level, "reasons": list(m.risk_reasons or [])}
    return out


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
    week_starts_on = _owner_week_start(db, owner_user_id)
    resolved = resolve_window(window, today, week_starts_on)
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
            report = build_volume_report(all_facts, today, resolved.range_key, week_starts_on)
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


# --- get_training_metric (#1071) ------------------------------------------------

# Bounds a breakdown: 60 weeks or 60 months is more than any chat answer reads.
_MAX_PERIODS = 60

# Narrows a measure to one kind of session, so "heart rate on my long runs" is
# one call rather than a list the coach filters by eye.
_SESSION_KINDS = {
    "long": lambda f: f.duration_class == "long",
    "intervals": lambda f: f.structure == "intervals",
    "races": lambda f: bool(f.is_race),
    "hilly": lambda f: bool(f.is_hilly),
}

_RUN_TYPES = {t.lower() for t in _TYPE_FILTER["run"]}


def _attach_check_ins(db: Session, owner_user_id, facts: List[Any]) -> None:
    """Fill each fact's check-in fields from the runner's latest check-in for that
    session. Not joined into the shared projection: a session can hold more than
    one check-in row, and a join would duplicate the session."""
    from app.models import CheckIn

    by_id = {f.activity_id: f for f in facts}
    if not by_id:
        return
    rows = (
        db.query(CheckIn)
        .join(Activity, Activity.id == CheckIn.activity_id)
        .filter(Activity.user_id == owner_user_id, CheckIn.activity_id.in_(list(by_id)))
        .order_by(CheckIn.created_at.asc())
        .all()
    )
    for row in rows:  # ascending, so the latest check-in wins
        fact = by_id[row.activity_id]
        fact.rpe = row.rpe
        fact.pain_score = row.pain_score


def _zones_note(db: Session, owner_user_id) -> dict:
    from app.services.coach.context import zones_calibration

    profile = db.query(UserProfile).filter(UserProfile.user_id == owner_user_id).first()
    calibrated, basis = zones_calibration(profile)
    out = {"zones_calibrated": calibrated, "zones_basis": basis}
    if not calibrated:
        out["zones_note"] = (
            "This runner's heart-rate zones are not calibrated, so zone figures are "
            "measured against default zones. Describe them as effort, not as zone numbers."
        )
    return out


def _is_full_period(lo: date, hi: date, group_by: str, week_starts_on: int) -> bool:
    if group_by == "week":
        return lo == week_start(lo, week_starts_on) and (hi - lo).days == 7
    if group_by == "month":
        return lo.day == 1 and hi.day == 1 and (hi - lo).days >= 28
    return True


def get_training_metric(
    db: Session, owner_user_id, *, metric: str, window: str,
    group_by: Optional[str], type_filter: Optional[str], min_zone: Any,
    session_kind: Optional[str], today: date,
) -> dict:
    """Any stored measure of the runner's training over a named window, whole or
    broken down by calendar week or month. The server picks how the measure
    combines and reports, for each period, how many sessions recorded it."""
    spec = training_metrics.BY_KEY.get(metric) if isinstance(metric, str) else None
    if spec is None:
        return {"error": "unknown_metric", "metric": str(metric), "allowed": list(training_metrics.BY_KEY)}
    group_by = group_by or "none"
    if group_by not in ("none", "week", "month"):
        return {"error": "unknown_group_by", "group_by": group_by, "allowed": ["none", "week", "month"]}
    if min_zone is None:
        zone = 2
    elif isinstance(min_zone, int) and not isinstance(min_zone, bool) and 1 <= min_zone <= 5:
        zone = min_zone
    else:
        return {"error": "invalid_min_zone", "min_zone": str(min_zone), "allowed": [1, 2, 3, 4, 5]}
    session_kind = session_kind or "all"
    if session_kind != "all" and session_kind not in _SESSION_KINDS:
        return {"error": "unknown_session_kind", "session_kind": session_kind,
                "allowed": ["all", *_SESSION_KINDS]}

    week_starts_on = _owner_week_start(db, owner_user_id)
    resolved = resolve_window(window, today, week_starts_on)
    if resolved is None:
        return {"error": "unknown_window", "window": window, "allowed": list(LIST_WINDOWS)}

    effective_filter = type_filter if type_filter not in (None, "") else spec.default_type
    types = _resolve_type_filter(effective_filter, db, owner_user_id)
    facts = _query_activity_facts(
        db, resolved.start, resolved.end, types=types,
        user_id=owner_user_id, include_session_shape=True,
    )
    if session_kind != "all":
        facts = [f for f in facts if _SESSION_KINDS[session_kind](f)]
    _attach_check_ins(db, owner_user_id, facts)

    coverage = _coverage(db, owner_user_id, resolved)
    records_begin = (
        date.fromisoformat(coverage["records_begin"]) if coverage.get("records_begin") else None
    )
    start = resolved.start or records_begin or today
    spans = training_metrics.periods(start, resolved.end, group_by, week_starts_on)
    if len(spans) > _MAX_PERIODS:
        return {
            "error": "too_many_periods",
            "periods": len(spans),
            "limit": _MAX_PERIODS,
            "hint": (
                "use a shorter window" if group_by == "month"
                else "use a shorter window or group_by month"
            ),
        }

    # A runner who also rides asking for "mileage" may mean either. Summed measures
    # over every activity type state the runs-only figure alongside, never merged.
    show_runs_only = (
        spec.combine == training_metrics.SUM
        and not types
        and any((f.activity_type or "").lower() not in _RUN_TYPES for f in facts)
    )

    def _measured(in_period: List[Any]) -> dict:
        out = training_metrics.measure(spec, in_period, zone)
        if show_runs_only:
            runs = [f for f in in_period if (f.activity_type or "").lower() in _RUN_TYPES]
            out["runs_only"] = training_metrics.measure(spec, runs, zone)["value"]
        return out

    out: dict = {
        "metric": spec.key,
        "measures": spec.description,
        "unit": spec.unit,
        "how_combined": training_metrics.HOW_COMBINED[spec.combine],
        "source": "measured from the runner's recorded sessions",
        "window": {
            "label": resolved.label,
            "type_filter": effective_filter or "all",
            "session_kind": session_kind,
            **coverage,
        },
    }
    if spec.default_type and type_filter in (None, ""):
        out["window"]["type_note"] = f"{spec.default_type}s only by default for this measure"
    if spec.key == "zone_time_h":
        out["min_zone"] = zone
        out.update(_zones_note(db, owner_user_id))

    out["whole_window"] = _measured(facts)
    if group_by != "none":
        rows = []
        for lo, hi in spans:
            row: dict = {"from": lo.isoformat(), "to": (hi - timedelta(days=1)).isoformat()}
            if records_begin is not None and hi <= records_begin:
                # Before the app holds any record: unknown, not a week off.
                row.update({"value": None, "before_records": True})
                rows.append(row)
                continue
            row.update(_measured([f for f in facts if lo <= f.local_date < hi]))
            if lo <= today < hi:
                row["in_progress"] = True
            elif not _is_full_period(lo, hi, group_by, week_starts_on):
                row["partial_period"] = f"covers {(hi - lo).days} days, clipped by the window"
            if records_begin is not None and lo < records_begin < hi:
                row["records_begin_mid_period"] = records_begin.isoformat()
            rows.append(row)
        out["group_by"] = group_by
        out["periods"] = rows
    if any("sessions_with_data" in r for r in [out["whole_window"], *out.get("periods", [])]):
        out["missing_data_note"] = (
            "sessions_with_data is lower than sessions where some sessions did not "
            "record this measure. Those sessions are left out of the figure, not counted as zero."
        )
    return out


def get_training_plan(db: Session, owner_user_id, *, today: Optional[date] = None) -> dict:
    """The runner's whole training block, week by week (#973).

    The coach receives this week of the plan in its baseline and nothing beyond,
    which is right for most turns and wrong for the one question a runner with a
    race asks most: what happens between here and the race. Asked that, the coach
    reached for training history, because no tool it held could return a plan, and
    then answered from the only thing in front of it. A live conversation produced
    "when you step up to the 16.5 km run on Aug 31" about a week the plan had
    written no sessions for at all.

    Built from `build_horizon`, the SAME builder behind the runner's own Schedule
    screen, so what the runner reads and what the coach reads are one answer
    rather than two that agree until they do not. It is a tool rather than more
    baseline because the block is irrelevant to most turns and the baseline is
    paid for on every one.

    Every week says which of two things it is. A `planned` week holds real
    sessions the runner has agreed to; a `sketched` week is shape, and shape is
    not a promise. Handing both over unlabelled is the same failure one level up:
    the coach would name a distance and a day for a week nobody has written, and
    it would sound exactly as confident as the truth.
    """
    from app.models.user import User
    from app.services.schedule.horizon import build_horizon

    today = today or date.today()
    user = db.query(User).filter(User.id == owner_user_id).first()
    if user is None:
        return {"error": "not_found"}

    horizon = build_horizon(db, user, today=today)
    if not horizon.has_plan:
        # A real answer, not an error: free mode is a destination. Saying it
        # plainly is what stops the coach reading an empty result as a fetch
        # failure and telling the runner to check another app.
        return {
            "has_plan": False,
            "week_count": 0,
            "note": (
                "This runner has no active training plan. They train without one, "
                "which is a supported way to use the app, and you can offer to "
                "write them one if the conversation reaches it."
            ),
        }

    weeks = []
    for week in horizon.weeks:
        if week.coverage == "beyond_plan":
            # Past the plan's own reach. Listing it would invite the coach to
            # describe a week the plan never claimed.
            continue
        entry: Dict[str, Any] = {
            "week_start": week.week_start.isoformat(),
            "is_this_week": week.is_current,
            "written": week.coverage == "planned",
            "phase": week.phase,
        }
        # `is not None`, not truthiness. A WRITTEN week holding no running is a
        # real answer (a cross-training week), and dropping the key entirely
        # would read as "unknown" rather than "none" to the one reader that
        # cannot ask a follow-up question.
        if week.running_distance_m is not None:
            entry["running_km"] = round(week.running_distance_m / 1000, 1)
        if week.walking_distance_m is not None:
            entry["walking_km"] = round(week.walking_distance_m / 1000, 1)
        if week.duration_s is not None:
            entry["hours_all_activities"] = round(week.duration_s / 3600, 1)
        if week.long_run_distance_m is not None:
            entry["long_run_km"] = round(week.long_run_distance_m / 1000, 1)
        if week.long_run_duration_s is not None:
            entry["long_run_minutes"] = round(week.long_run_duration_s / 60)
        if week.quality_focus:
            entry["quality_focus"] = week.quality_focus
        if week.challenges:
            from app.services.schedule.coach_view import challenge_line

            entry["challenges"] = [challenge_line(c) for c in week.challenges]
        if week.coverage == "empty":
            entry["note"] = "the plan says nothing about this week"
        elif week.coverage == "sketched":
            entry["note"] = (
                "shape only: agreed in outline, no sessions written yet"
            )
        weeks.append(entry)

    # Each goal states how exact its date is (#1042), so an approximate month
    # on the horizon is never read as a booked race.
    races = [goals.for_coach(race, today) for race in horizon.races]
    written = [w for w in weeks if w["written"]]
    from app.services.schedule.coach_view import season_section

    return {
        "has_plan": True,
        **season_section(db, user, today),
        **(
            {"plan_is_still_short_of": horizon.shortfalls}
            if horizon.shortfalls
            else {}
        ),
        "today": today.isoformat(),
        "week_count": len(weeks),
        "weeks": weeks,
        "races": races,
        # Two different facts, and the coach was previously given only the first
        # (#981). A plan can run to October while telling the runner what to do
        # only until the end of this month.
        #
        # Both are WEEK STARTS and say so in their names. The baseline states the
        # same two facts as DAYS (`runs_through`, `sessions_written_through`), and
        # a coach holding "2026-10-05" and "2026-10-11" for what sounds like one
        # question will eventually pick the wrong one to say out loud.
        "plan_covers_through_week_starting": weeks[-1]["week_start"] if weeks else None,
        "sessions_written_through_week_starting": (
            written[-1]["week_start"] if written else None
        ),
        "how_to_read": (
            "A week marked written holds real sessions this runner has agreed to. "
            "A week marked shape only is a direction you both settled, not a "
            "prescription: say what it is FOR, and never name a session, a day or "
            "a distance in it as though it were written. If the runner needs one "
            "of those weeks written out, offer amend_plan."
        ),
    }


def get_personal_bests(db: Session, owner_user_id) -> dict:
    """The runner's PBs at the distances runners race (#1068), each labelled with
    how far it can be trusted; see `app.services.personal_bests`."""
    from app.services.personal_bests import for_runner

    return for_runner(db, owner_user_id)


# --- tool schemas + dispatch -------------------------------------------------

# The status label shown in chat while each tool runs (the ephemeral "fetching"
# affordance, #648). Coach-framed, so it reads as a competent coach checking the
# record rather than a spinner.
TOOL_STATUS_LABELS = {
    "list_activities_in_range": "Checking your training history…",
    "get_session_detail": "Pulling up that session…",
    "get_training_summary": "Tallying your recent training…",
    "get_training_plan": "Reading your training plan…",
    "get_personal_bests": "Looking up your personal bests…",
    "get_training_metric": "Measuring your training…",
}


# The past-tense verb shown on the PERSISTENT trace of a finished turn (#664, the
# #663 follow-up). Owned here alongside the present-tense spinner labels so the two
# tenses live in ONE module instead of being duplicated across the backend spinner
# and the frontend trace (the #663 friendly-label duplication). An unknown key falls
# back to a generic phrasing so a new tool never renders a raw identifier.
TOOL_TRACE_LABELS = {
    "list_activities_in_range": "Looked up your training history",
    "get_session_detail": "Pulled up a past session",
    "get_training_summary": "Tallied your recent training",
    "get_training_plan": "Read your training plan",
    "get_personal_bests": "Looked up your personal bests",
    "get_training_metric": "Measured your training",
    # The server-side web search (#1051). Its detail is a result count, server-derived.
    "web_search": "Searched the web",
}
_DEFAULT_TRACE_LABEL = "Looked up your training data"


# Compact, human-readable renderings of each named window, for the trace's `detail`
# (#664). These are SHORT ("last 30 days") rather than the tool result's verbose
# `window.label` ("the last 30 days (2026-06-21 to 2026-07-21)"), which is meant for
# the coach to reason over. Keyed by the window enum value the model supplied, so the
# label is server-derived from a server-known token, never from model prose.
WINDOW_TRACE_LABELS = {
    "last_7_days": "last 7 days",
    "last_14_days": "last 14 days",
    "last_30_days": "last 30 days",
    "last_90_days": "last 90 days",
    "last_180_days": "last 180 days",
    "last_365_days": "last year",
    "this_week": "this week",
    "last_week": "last week",
    "this_month": "this month",
    "last_month": "last month",
    "this_year": "this year",
    "all_time": "all time",
    "last_4_weeks": "last 4 weeks",
    "last_8_weeks": "last 8 weeks",
    "last_12_weeks": "last 12 weeks",
}


# How each modality filter reads in a trace (#886). Keyed by the same tokens
# `_TYPE_FILTER` accepts, so the chip is server-derived from a server-known
# value: a `type_filter` the tool did not recognise renders as nothing rather
# than putting the model's own word in front of the runner.
TYPE_FILTER_TRACE_LABELS = {
    "run": "runs",
    "ride": "rides",
    "strength": "strength sessions",
    "swim": "swims",
    "walk": "walks",
}


def trace_label(name: Optional[str]) -> str:
    """The past-tense trace verb for a tool name (or the generic fallback)."""
    return TOOL_TRACE_LABELS.get(name or "", _DEFAULT_TRACE_LABEL)


def _window_detail(tool_input: Dict[str, Any]) -> Optional[str]:
    """What was looked at: the window, and WHICH sessions in it (#886).

    The filter used to be dropped, so two lookups over the same window returned
    contradictory counts under identical chips — "last 30 days (17 sessions)"
    and "last 30 days (55 sessions)" on the same day, one runs-only and one
    everything. Both were right. The chip exists so the runner can sanity-check
    what the coach read, and two different questions that look like one question
    do the opposite of that.
    """
    window = WINDOW_TRACE_LABELS.get(tool_input.get("window"))
    if window is None:
        return None
    modality = TYPE_FILTER_TRACE_LABELS.get(tool_input.get("type_filter"))
    return f"{modality}, {window}" if modality else window


def summarize_tool_call(
    name: Optional[str], tool_input: Optional[Dict[str, Any]], result: Optional[dict]
) -> dict:
    """Derive one compact, safe trace record from a completed tool call (#664).

    Produces `{tool, label, detail, count}` describing WHAT was fetched — the resolved
    window and a result count — so the runner can sanity-check the data the coach
    reasoned over instead of seeing only that a tool ran. Every field is SERVER-derived
    (the past-tense verb, the window enum humanised, counts computed by the tool); no
    model-authored prose enters the trace, so it needs no policy gate (it is not the
    coach's reply). One record per tool CALL, so a genuine multi-window turn no longer
    collapses to a single chip. Never raises: a malformed or error result degrades to a
    label-only record."""
    tool_input = tool_input or {}
    result = result or {}
    entry: dict = {"tool": name or "", "label": trace_label(name), "detail": None, "count": None}

    def _int(value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if name == "get_training_metric":
        detail = _window_detail(tool_input)
        what = training_metrics.BY_KEY.get(tool_input.get("metric"))
        if what is not None and what.key == "zone_time_h":
            noun = f"zone {result.get('min_zone', 2)}+ time"
        else:
            noun = what.description if what is not None else None
        entry["detail"] = f"{noun}, {detail}" if noun and detail else detail
        entry["count"] = _int((result.get("whole_window") or {}).get("sessions"))
    elif name in ("list_activities_in_range", "get_training_summary"):
        entry["detail"] = _window_detail(tool_input)
        if name == "list_activities_in_range":
            entry["count"] = _int(result.get("count"))
        else:  # get_training_summary: the session total is the natural count
            entry["count"] = _int((result.get("totals") or {}).get("sessions"))
    elif name == "get_session_detail":
        # A single named session: its local date is the useful "what", no count.
        d = result.get("date")
        entry["detail"] = d if isinstance(d, str) else None
    elif name == "get_training_plan":
        # The block, not a window: what the runner can check is how far ahead the
        # coach actually read, and how many of those weeks hold real sessions.
        # The unit here is WEEKS, and the client renders a bare `count` as
        # "sessions", so the number goes in the detail where it can carry its
        # own noun. A chip reading "7 sessions" for a seven-WEEK block is the
        # kind of small false number this trace exists to prevent.
        through = result.get("plan_covers_through_week_starting")
        weeks = _int(result.get("week_count"))
        parts = []
        if weeks:
            parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
        if isinstance(through, str):
            parts.append(f"to {through}")
        entry["detail"] = ", ".join(parts) or None
    elif name == "get_personal_bests":
        # The distances the coach actually saw, in the detail for the same reason
        # as the plan: a bare count renders as "sessions".
        from app.services.personal_bests import DISPLAY_NAMES

        found = [
            DISPLAY_NAMES.get(p.get("distance"), p.get("distance"))
            for p in (result.get("personal_bests") or [])
            if isinstance(p, dict) and isinstance(p.get("distance"), str)
        ]
        entry["detail"] = ", ".join(found) or "none on record"
    return entry


CHAT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_training_plan",
        "description": (
            "Read this runner's training plan: every week from now to the end of "
            "the block, with its phase, its running distance, how far its long "
            "run goes, and whether it holds real sessions or is shape only. Use "
            "this for any question about what is COMING — the block ahead, the "
            "weeks between now and their race, what a future week is for, "
            "whether the plan still covers them. Your other tools read what they "
            "have already DONE and cannot answer those. Each week says whether it "
            "is written or shape only: never name a session, a day or a distance "
            "in a shape-only week as though it were written."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
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
    {
        "name": "get_training_metric",
        "description": (
            "Measure ANY recorded aspect of the runner's training over a named window, "
            "as one figure or broken down by calendar week or month: distance, time, "
            "elevation, time at or above a heart-rate zone, training load, average or "
            "highest heart rate, pace, cadence, heart-rate drift, pace variability, "
            "temperature, sessions by effort, counts of interval, long, hilly and race "
            "sessions, and how hard sessions felt and any pain, from the runner's own "
            "check-ins. Use it whenever a question turns on a number from their record "
            "that your other tools do not state directly, for example 'how much zone 2+ "
            "did I do each week' (zone_time_h, last_4_weeks, by week) or 'has my "
            "long-run heart rate come down this year' (avg_hr_bpm, this_year, by month, "
            "session_kind long). The server combines the figure and says how; each period also says "
            "how many sessions recorded the measure, so a missing heart-rate strap reads "
            "as missing data, never as an easy week. Pick the window by name; never "
            "compute dates."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": list(training_metrics.BY_KEY),
                    "description": "What to measure.",
                },
                "window": {
                    "type": "string",
                    "enum": list(LIST_WINDOWS),
                    "description": "The named time window, chosen to match the runner's phrasing. The last_N_weeks windows are whole calendar weeks, the right choice for a week-by-week read.",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["none", "week", "month"],
                    "description": "One figure for the whole window (none, the default), or one per calendar week or month.",
                },
                "type_filter": {
                    "type": "string",
                    "enum": ["all", "run", "ride", "strength", "swim", "walk"],
                    "description": "Optional modality filter. Pace and cadence default to runs; everything else defaults to all activity.",
                },
                "session_kind": {
                    "type": "string",
                    "enum": ["all", "long", "intervals", "races", "hilly"],
                    "description": "Optional: measure only one kind of session. Defaults to all.",
                },
                "min_zone": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "For zone_time_h only: the lowest heart-rate zone counted (2 = zone 2 or above). Defaults to 2.",
                },
            },
            "required": ["metric", "window"],
        },
    },
    {
        "name": "get_personal_bests",
        "description": (
            "Read this runner's personal bests at 1 mile, 5K, 10K, half marathon "
            "and marathon. Use it whenever an answer turns on what they have "
            "already run at a distance: race pacing, whether a goal time is "
            "realistic, how a run compares with their best, or a direct question "
            "about their PBs. Each entry carries a reading saying how far its "
            "time can be trusted: follow it, above all when it says the time is "
            "NOT their PB. A distance in no_record_at has nothing on record: ask "
            "them rather than estimating it from their runs."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
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
        if name == "get_training_metric":
            return get_training_metric(
                db, owner_user_id,
                metric=tool_input.get("metric", ""),
                window=tool_input.get("window", ""),
                group_by=tool_input.get("group_by"),
                type_filter=tool_input.get("type_filter"),
                min_zone=tool_input.get("min_zone"),
                session_kind=tool_input.get("session_kind"),
                today=today,
            )
        if name == "get_training_plan":
            return get_training_plan(db, owner_user_id, today=today)
        if name == "get_personal_bests":
            return get_personal_bests(db, owner_user_id)
        return {"error": "unknown_tool", "tool": name}
    except Exception as exc:  # graceful degrade — the coach answers from what it has
        logger.warning("chat tool %s failed: %s", name, exc)
        return {"error": "fetch_failed", "tool": name}
