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
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Activity, UserProfile
from app.models.derived_metric import DerivedMetric
from app.services.coach import coach_units, training_metrics
from app.services.analysis.splits import calculate_splits
from app.services.units.cadence import normalize_cadence_spm
from app.services.coach.recent_training import _interval_shape
from app.services.coach.volume import build_volume_report
from app.services.activity_facts import query_facts as _query_activity_facts
from app.services.weeks import week_start

# Re-imported so every name other modules, tests and the diagram generator
# import from here still resolves (#1102).
from app.services.coach.query_tools_windows import (  # noqa: F401
    LIST_WINDOWS,
    SUMMARY_WINDOWS,
    ResolvedWindow,
    _LISTED_ELSEWHERE,
    _MAX_LIST_ACTIVITIES,
    _PER_SESSION_NAME,
    _ROLLING_DAYS_RANGEKEY,
    _TYPE_FILTER,
    _WHOLE_WEEKS,
    _coverage,
    _duration,
    _first_of_month,
    _km,
    _owner_week_start,
    _pace,
    _records_begin,
    _resolve_type_filter,
    _session_measures,
    resolve_window,
)
from app.services.coach.query_tools_plan import get_personal_bests, get_training_plan  # noqa: F401
from app.services.coach.query_tools_labels import (  # noqa: F401
    TOOL_STATUS_LABELS,
    TOOL_TRACE_LABELS,
    TYPE_FILTER_TRACE_LABELS,
    WINDOW_TRACE_LABELS,
    _DEFAULT_TRACE_LABEL,
    _window_detail,
    summarize_tool_call,
    trace_label,
)

logger = logging.getLogger(__name__)


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


# The per-km splits summary in a session detail is capped, as a list result is,
# so a wide window cannot flood the context.
_MAX_SPLIT_ROWS = 30


def _as_uuid(value) -> Optional[uuid.UUID]:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


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


# --- tool schemas + dispatch -------------------------------------------------


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
