"""ADR 0026 Slice 2 (#670): the day-resolved `recent_weeks` coach-pack section.

The single "recent training" read that MERGES the retired `training_volume` +
`recent_training` sections into one picture on one week model, fixing the #653
rolling-vs-partial-calendar-week frame confusion at the DATA level:

  - `rolling_7d` is the LOAD-VERDICT lane — a full 7 days ending on this run, so it
    compares directly to the runner's weekly norm (no pro-rating). It carries the firm
    vs-your-typical verdict and pairs with `readiness`.
  - `this_week` / `last_week` are the STRUCTURE lane — the calendar weeks, day by day
    (rest days included), each day with its activities and totals. `this_week` is
    partial and carries NO verdict (the #653 fix — no fudged partial-week read);
    complete weeks carry `vs_your_typical`.

The day rows are the single source of truth; the window/rolling totals are sums over
them, so nothing is duplicated. Per-activity rows are lean-but-honest: the SHAPE plus
the raw felt-vs-measured signals (HR-derived `intensity`, felt `rpe`, `avg_hr`,
`hr_drift`), so a coach can see where a stimulant/heat inflated HR versus how the run
actually felt — every field dropped when it does not apply.

"typical" reuses the volume core's clamped per-day-rate definition, so it means one
thing across the product (#451). Pure functions over already-fetched facts (no DB, no
LLM); the DB read + pack wiring live in `context.py`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List, NamedTuple, Optional, Union

from app.services.weeks import MONDAY, days_into_week, is_last_day_of_week, week_start

from app.schemas.coach_context import (
    RecentWeeksActivity,
    RecentWeeksAllTotal,
    RecentWeeksCalendar,
    RecentWeeksContext,
    RecentWeeksDay,
    RecentWeeksDayTotals,
    RecentWeeksEndpoint,
    RecentWeeksRolling,
    RecentWeeksTotals,
    RecentWeeksTypeTotal,
    RecentWeeksVsTypical,
    RecentWeeksVsTypicalMetric,
)
from app.services.coach.recent_training import _interval_shape
from app.services.coach.volume import (
    _BASELINE_WEEKS,
    _baseline_window,
    _direction,
    _is_run,
    _norm_per_day,
    _sum,
)

# A check-in bounded to what the recent log needs (the coach reads felt effort + a pain
# flag + short notes; the subject run's full check-in lives in `this_run`). Keyed by
# activity_id, built by context.py from a narrow supplementary read.
class CheckInFacts(NamedTuple):
    rpe: Optional[int]
    pain_score: Optional[int]
    notes: Optional[str]


# Bound the per-session notes so a long free-text entry cannot bloat the log; the full
# note reaches the coach elsewhere (the subject run's check-in, the memory profile).
_MAX_NOTE_CHARS = 200

# The four vs-typical metrics, mapped to the raw fact metric they sum and the display
# unit. "typical" reuses the volume core so it means one thing across the product.
# (key, raw_metric, unit): unit in {"count", "km", "duration", "load"}.
_VS_METRICS = (
    ("sessions", "sessions", "count"),
    ("distance", "distance_m", "km"),
    ("duration", "moving_time_s", "duration"),
    ("load", "effort_score", "load"),
)


def _fmt_duration(seconds: Optional[float]) -> str:
    s = int(round(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def _fmt_date(d: date) -> str:
    return d.strftime("%d-%m-%y")


def _endpoint(d: date) -> RecentWeeksEndpoint:
    return RecentWeeksEndpoint(weekday=d.strftime("%a"), date=_fmt_date(d))


def _display(value: float, unit: str) -> Union[int, float, str]:
    if unit == "count":
        return int(round(value))
    if unit == "km":
        return round(value / 1000.0, 1)
    if unit == "duration":
        return _fmt_duration(value)
    return round(value)  # load


def _by_type_totals(window_facts: List[Any]) -> List[RecentWeeksTypeTotal]:
    """Per-activity-type totals + the type's session share, most-frequent-first (ties
    broken by type name for determinism)."""
    groups: dict = {}
    for f in window_facts:
        t = f.activity_type or "unknown"
        g = groups.setdefault(
            t, {"sessions": 0, "distance_m": 0.0, "moving_time_s": 0.0, "load": 0.0}
        )
        g["sessions"] += 1
        g["distance_m"] += f.distance_m or 0
        g["moving_time_s"] += f.moving_time_s or 0
        g["load"] += f.effort_score or 0
    total = len(window_facts)
    out: List[RecentWeeksTypeTotal] = []
    for t in sorted(groups, key=lambda k: (-groups[k]["sessions"], k)):
        g = groups[t]
        out.append(
            RecentWeeksTypeTotal(
                type=t,
                sessions=g["sessions"],
                distance_km=round(g["distance_m"] / 1000.0, 1),
                duration=_fmt_duration(g["moving_time_s"]),
                load=round(g["load"]),
                share_pct=round(g["sessions"] / total * 100.0, 1) if total else 0.0,
            )
        )
    return out


def _totals(window_facts: List[Any]) -> RecentWeeksTotals:
    return RecentWeeksTotals(
        all=RecentWeeksAllTotal(
            sessions=len(window_facts),
            distance_km=round(_sum(window_facts, "distance_m") / 1000.0, 1),
            duration=_fmt_duration(_sum(window_facts, "moving_time_s")),
            load=round(_sum(window_facts, "effort_score")),
        ),
        by_type=_by_type_totals(window_facts),
    )


def _interval_source(interval_structure: Any) -> Optional[str]:
    """How this past session's interval structure was detected — "recorded_laps" when
    the runner pressed the lap button, else None. Mirrors recent_training._interval_source,
    reusing the exact `metrics.interval_structure.source` vocabulary the subject-run rule
    keys on, so the coach reads a past session the same way (#712)."""
    if not isinstance(interval_structure, dict):
        return None
    return interval_structure.get("source")


def _activity(fact: Any, checkins: dict) -> RecentWeeksActivity:
    ci: Optional[CheckInFacts] = checkins.get(getattr(fact, "activity_id", None))
    is_run = _is_run(fact)
    note = ci.notes if ci else None
    if note:
        note = note.strip()[:_MAX_NOTE_CHARS] or None
    return RecentWeeksActivity(
        type=fact.activity_type or "unknown",
        distance_km=round(fact.distance_m / 1000.0, 1) if fact.distance_m else None,
        duration=_fmt_duration(fact.moving_time_s) if fact.moving_time_s else None,
        intensity=getattr(fact, "effort", None),
        avg_hr=fact.avg_hr,
        rpe=ci.rpe if ci else None,
        load=round(fact.effort_score) if fact.effort_score is not None else None,
        elev_gain_m=round(fact.elev_gain_m) if fact.elev_gain_m else None,
        hr_drift=(
            round(fact.hr_drift, 1)
            if getattr(fact, "hr_drift", None) is not None
            else None
        ),
        structure=(getattr(fact, "structure", None) if is_run else None),
        shape=(_interval_shape(getattr(fact, "interval_structure", None)) if is_run else None),
        source=(_interval_source(getattr(fact, "interval_structure", None)) if is_run else None),
        long_run=(True if (is_run and getattr(fact, "duration_class", None) == "long") else None),
        pain=ci.pain_score if ci else None,
        notes=note,
    )


def _days(
    win_start: date, win_end: date, window_facts: List[Any], checkins: dict
) -> List[RecentWeeksDay]:
    by_date: dict = {}
    for f in window_facts:
        by_date.setdefault(f.local_date, []).append(f)
    out: List[RecentWeeksDay] = []
    d = win_start
    while d <= win_end:
        facts_d = by_date.get(d, [])
        if not facts_d:
            out.append(RecentWeeksDay(weekday=d.strftime("%a"), date=_fmt_date(d), rest=True))
        else:
            out.append(
                RecentWeeksDay(
                    weekday=d.strftime("%a"),
                    date=_fmt_date(d),
                    activities=[_activity(f, checkins) for f in facts_d],
                    day_totals=RecentWeeksDayTotals(
                        distance_km=round(sum(f.distance_m or 0 for f in facts_d) / 1000.0, 1),
                        duration=_fmt_duration(sum(f.moving_time_s or 0 for f in facts_d)),
                        load=round(sum(f.effort_score or 0 for f in facts_d)),
                    ),
                )
            )
        d += timedelta(days=1)
    return out


def _vs_typical(
    window_facts: List[Any], all_facts: List[Any], baseline_end: date
) -> Optional[RecentWeeksVsTypical]:
    """The four-metric current-vs-typical verdict for a FULL 7-day window, against the
    runner's own per-day rate over the ~12 weeks ending `baseline_end` (the volume core's
    clamped definition, so "typical" means one thing across the product). None when the
    baseline is too thin to resolve a norm (the section's `has_baseline` then reads
    False)."""
    bl = _baseline_window(all_facts, baseline_end, _BASELINE_WEEKS * 7)
    if bl is None:
        return None
    per_day = _norm_per_day(all_facts, *bl)
    if all(v is None for v in per_day.values()):
        return None

    metrics: dict = {}
    for key, raw, unit in _VS_METRICS:
        current = _sum(window_facts, raw)
        norm_weekly = per_day[raw] * 7 if per_day[raw] is not None else None
        pct = (
            round((current - norm_weekly) / norm_weekly * 100.0, 1)
            if norm_weekly
            else None
        )
        metrics[key] = RecentWeeksVsTypicalMetric(
            current=_display(current, unit),
            typical=(_display(norm_weekly, unit) if norm_weekly is not None else None),
            direction=_direction(current, norm_weekly, 1.0),
            pct=pct,
        )
    return RecentWeeksVsTypical(**metrics)


def build_recent_weeks(
    facts: List[Any], checkins: dict, as_of: date, week_starts_on: int = MONDAY
) -> RecentWeeksContext:
    """Build the day-resolved recent-weeks read as of `as_of` (the runner's local day)
    from facts spanning at least the trailing ~91 days (the two weeks of structure plus
    the ~12-week norm baseline). Facts are duck-typed `ActivityFact`s; `checkins` maps
    `activity_id -> CheckInFacts` for the per-activity felt-effort signals.

    `week_starts_on` (0=Monday default, 6=Sunday) sets the calendar-week boundary so
    "this week" / "last week" match the runner's chosen week start (#676); the rolling
    7d lane is week-start-independent."""
    # rolling 7d — the load-verdict lane (full 7 days, directly comparable).
    roll_start = as_of - timedelta(days=6)
    roll_facts = [f for f in facts if roll_start <= f.local_date <= as_of]
    rolling = RecentWeeksRolling(
        start=_endpoint(roll_start),
        end=_endpoint(as_of),
        label="Trailing 7 days, as of this run",
        totals=_totals(roll_facts),
        # The norm reads history strictly before the trailing 7 days (mirrors
        # build_training_volume's rolling baseline_end).
        vs_your_typical=_vs_typical(roll_facts, facts, as_of - timedelta(days=7)),
    )

    # this week — week start to the run's day (partial unless the run is the last day
    # of the runner's week).
    this_start = week_start(as_of, week_starts_on)
    this_complete = is_last_day_of_week(as_of, week_starts_on)
    this_facts = [f for f in facts if this_start <= f.local_date <= as_of]
    this_week = RecentWeeksCalendar(
        start=_endpoint(this_start),
        end=_endpoint(as_of),
        label="This week" if this_complete else "This week, in progress",
        complete=this_complete,
        days_elapsed=days_into_week(as_of, week_starts_on),
        days=_days(this_start, as_of, this_facts, checkins),
        week_totals=_totals(this_facts),
        # A partial week carries NO verdict (the #653 fix); a complete Sunday week does.
        vs_your_typical=(
            _vs_typical(this_facts, facts, this_start - timedelta(days=1))
            if this_complete
            else None
        ),
    )

    # last week — the previous complete week block (ends the day before this_start).
    last_end = this_start - timedelta(days=1)
    last_start = last_end - timedelta(days=6)
    last_facts = [f for f in facts if last_start <= f.local_date <= last_end]
    last_week = RecentWeeksCalendar(
        start=_endpoint(last_start),
        end=_endpoint(last_end),
        label="Last week",
        complete=True,
        days_elapsed=7,
        days=_days(last_start, last_end, last_facts, checkins),
        week_totals=_totals(last_facts),
        vs_your_typical=_vs_typical(last_facts, facts, last_start - timedelta(days=1)),
    )

    has_baseline = rolling.vs_your_typical is not None and any(
        m.direction != "no_norm"
        for m in (
            rolling.vs_your_typical.sessions,
            rolling.vs_your_typical.distance,
            rolling.vs_your_typical.duration,
            rolling.vs_your_typical.load,
        )
    )

    return RecentWeeksContext(
        rolling_7d=rolling,
        this_week=this_week,
        last_week=last_week,
        has_baseline=has_baseline,
    )
