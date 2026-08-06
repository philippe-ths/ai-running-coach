"""#444: the modality-aware recent-training picture for the coach pack.

The coach pack's single recent-training picture (#451: it replaced the coarse
`recent_training_summary`, now retired, so there is one recent-volume home). For
each of three windows — recent (last 7 days), recent long (last 30 days) and the
prior comparison window (the 30 days before that) — it reports:

  - a per-activity-TYPE breakdown (counts + per-type distance/time/load totals), so
    a walk is never read as a run and the coach sees the full cardio picture;
  - a precomputed per-type SHARE of the window (the modality mix as a number to
    read, not a division the model has to do);
  - overall roll-up totals.

The recent (7d) window also carries a bounded, capped per-activity list (each
session's type and intensity/load), so the coach can speak to specific sessions.

Both the 7d and 30d windows carry a vs-PREV comparison per metric; only the 30d
window carries a vs-TYPICAL one (#451: the 7d weekly vs-norm verdict lives in the
`training_volume` section, so recent_training does not duplicate it). All
percentages are precomputed with a deadband direction (up/in_line/down/no_norm),
each tagged with a self-describing BASIS so the coach never cites a comparison whose
reference it does not know:

  - "typical" (30d only) is the runner's own average daily rate over a trailing
    ~6-month baseline, projected onto the window — the SAME clamped per-day-rate
    definition the Trends page and `training_volume` use, reused via
    `build_volume_report`, so "typical" has one meaning across the product (#451).
  - "prev" is the same metric over the equal-length window immediately before this
    one.

Pure functions over already-fetched `ActivityFact`s (no DB, no LLM); the DB read
and pack wiring live in `context.py`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List

from app.schemas.coach_context import (
    RecentActivityItem,
    RecentComparison,
    RecentTrainingContext,
    RecentTrainingWindow,
    RecentTypeBreakdown,
)
from app.services.activity_facts import METRICS as _METRICS
from app.services.activity_facts import direction as _direction
from app.services.activity_facts import sum_metric as _sum
from app.services.coach.volume import build_volume_report

# Cap on the per-activity list for the recent window (bounded by design — the longer
# windows carry only the per-type roll-up, never a per-session list).
_MAX_RECENT_ACTIVITIES = 12

# Self-describing basis for the vs-typical comparison. Only the 30d window carries a
# vs-typical read now (#451): the 7d weekly vs-norm verdict lives in `training_volume`,
# so recent_training does not duplicate it. Mirrors the per-day-rate definition
# build_volume_report computes (~6 months for 30d), so the label and the number agree.
_TYPICAL_BASIS = {
    30: (
        "your own average daily training over the last ~6 months, projected onto "
        "30 days (rest days counted as zero)"
    ),
}


def _unknown_type(fact: Any) -> str:
    return (getattr(fact, "activity_type", None) or "unknown")


def _interval_shape(interval_structure: Any) -> Optional[str]:
    """#650: a compact rep shape ("7x400m") for a past session's per-session marker,
    read from the stored `interval_structure`'s work segments so the coach can see at a
    glance what a recent interval session was (cadence tracking, plan recognition)
    without re-deriving it. Returns None when there is no rep structure to describe.

    Distances are clustered to the nearest 50m so honest GPS jitter (398/402m) reads as
    "400m"; when the reps carry no usable distance (time-based reps) it falls back to a
    plain rep count ("6 reps")."""
    if not isinstance(interval_structure, dict):
        return None
    segments = interval_structure.get("work_segments") or []
    n = len(segments)
    if n == 0:
        return None
    dists = [s.get("distance_m") for s in segments if isinstance(s, dict) and s.get("distance_m")]
    if not dists:
        return f"{n} reps"
    dists.sort()
    median = dists[len(dists) // 2]
    rounded = int(round(median / 50.0)) * 50
    if rounded <= 0:
        return f"{n} reps"
    return f"{n}x{rounded}m"


def _interval_source(interval_structure: Any) -> Optional[str]:
    """#661: how a past session's interval structure was detected. Returns
    "recorded_laps" when the runner pressed the lap button (the stored
    `interval_structure.source`), so a later report knows not to advise the lap
    button on that session. Returns None for a stream-detected interval session or
    a non-interval session — the same absence-means-not-lap-recorded reading the
    subject-run rule uses (`metrics.interval_structure.source == "recorded_laps"`)."""
    if not isinstance(interval_structure, dict):
        return None
    return interval_structure.get("source")


def _by_type(window_facts: List[Any]) -> List[RecentTypeBreakdown]:
    """Per-activity-type counts + totals + the type's session share of the window,
    most-frequent type first (ties broken by type name for determinism)."""
    groups: dict = {}
    for f in window_facts:
        t = _unknown_type(f)
        g = groups.setdefault(
            t, {"count": 0, "distance_m": 0.0, "moving_time_s": 0.0, "effort_score": 0.0}
        )
        g["count"] += 1
        g["distance_m"] += f.distance_m or 0
        g["moving_time_s"] += f.moving_time_s or 0
        g["effort_score"] += f.effort_score or 0

    total = len(window_facts)
    out: List[RecentTypeBreakdown] = []
    for t in sorted(groups, key=lambda k: (-groups[k]["count"], k)):
        g = groups[t]
        out.append(
            RecentTypeBreakdown(
                type=t,
                count=g["count"],
                distance_m=int(g["distance_m"]),
                moving_time_s=int(g["moving_time_s"]),
                effort_score=round(g["effort_score"], 1),
                share_pct=round(g["count"] / total * 100.0, 1) if total else 0.0,
            )
        )
    return out


def _recent_activities(window_facts: List[Any]) -> List[RecentActivityItem]:
    """The recent window's sessions, newest-first and capped, each with its type and
    intensity/load read (the per-activity detail the longer windows omit)."""
    ordered = sorted(
        window_facts, key=lambda f: (f.local_date, getattr(f, "activity_id", None) is None)
    )
    ordered = list(reversed(ordered))[:_MAX_RECENT_ACTIVITIES]
    return [
        RecentActivityItem(
            date=f.local_date.isoformat(),
            type=_unknown_type(f),
            effort=getattr(f, "effort", None),  # HR-derived intensity axis, may be None
            effort_score=round(f.effort_score, 1) if f.effort_score is not None else None,
            # #647: per-run volume from the same fact; facts carry these (default 0).
            distance_m=int(f.distance_m) if f.distance_m is not None else None,
            moving_time_s=int(f.moving_time_s) if f.moving_time_s is not None else None,
            # #650: weekday as given data + structure + compact rep shape, plus the
            # runner-relative "long run" verdict surfaced only when long.
            weekday=f.local_date.strftime("%a"),
            structure=getattr(f, "structure", None),
            interval_shape=_interval_shape(getattr(f, "interval_structure", None)),
            # #661: carry the lap-button provenance so a later report engaging this
            # past session's thread never advises the lap button on a recorded-laps run.
            source=_interval_source(getattr(f, "interval_structure", None)),
            long_run=True if getattr(f, "duration_class", None) == "long" else None,
        )
        for f in ordered
    ]


def _vs_prev_pct(window_facts: List[Any], prev_facts: List[Any], metric: str):
    """(pct, direction) of the current window vs the immediately-prior equal window
    for one metric, or (None, 'no_norm') when the prior window is empty for it."""
    prev = _sum(prev_facts, metric)
    if prev <= 0:
        return None, "no_norm"
    cur = _sum(window_facts, metric)
    pct = round((cur - prev) / prev * 100.0, 1)
    return pct, _direction(cur, prev, 1.0)


def _comparisons(
    facts: List[Any], as_of: date, n: int, *, with_typical: bool
) -> List[RecentComparison]:
    """The vs-prev comparison per metric for a window of `n` days ending `as_of`, plus
    the vs-typical (per-day-rate, reused from build_volume_report) ONLY when
    `with_typical` (the 30d window). The 7d window omits vs-typical (#451): its weekly
    vs-norm verdict lives in `training_volume`, so those rows carry only vs-prev and a
    null vs-typical direction.

    Pack-trimmed: the per-metric current_all/current_runs (already in the window roll-up
    + by_type) and the self-describing basis strings (now carried once per window, set by
    `_window`) are NOT re-emitted on each row. (This section is now the single home for
    the trailing-7d numbers — training_volume.rolling_7d carries the vs-norm verdict
    only, with its raw current values folded out.)"""
    typical_by_metric: dict = {}
    if with_typical:
        range_key = "7D" if n == 7 else "30D"
        report = build_volume_report(facts, as_of, range_key)
        typical_by_metric = {m.metric: m for m in report.rolling.metrics}

    cur_start = as_of - timedelta(days=n - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=n - 1)
    window_facts = [f for f in facts if cur_start <= f.local_date <= as_of]
    prev_facts = [f for f in facts if prev_start <= f.local_date <= prev_end]

    out: List[RecentComparison] = []
    for metric in _METRICS:
        t = typical_by_metric.get(metric)
        prev_pct, prev_dir = _vs_prev_pct(window_facts, prev_facts, metric)
        out.append(
            RecentComparison(
                metric=metric,
                vs_typical_pct=t.pct_vs_norm if t else None,
                vs_typical_direction=t.direction if t else None,
                vs_prev_pct=prev_pct,
                vs_prev_direction=prev_dir,
            )
        )
    return out


def _window(
    name: str,
    n: int,
    win_start: date,
    win_end: date,
    facts: List[Any],
    *,
    as_of: date,
    with_comparisons: bool,
    with_activities: bool,
    with_typical: bool = False,
) -> RecentTrainingWindow:
    window_facts = [f for f in facts if win_start <= f.local_date <= win_end]
    return RecentTrainingWindow(
        window=name,
        days=n,
        activity_count=len(window_facts),
        by_type=_by_type(window_facts),
        total_distance_m=int(_sum(window_facts, "distance_m")),
        total_moving_time_s=int(_sum(window_facts, "moving_time_s")),
        total_effort=round(_sum(window_facts, "effort_score"), 1),
        comparisons=(
            _comparisons(facts, as_of, n, with_typical=with_typical)
            if with_comparisons
            else []
        ),
        activities=_recent_activities(window_facts) if with_activities else [],
        # Pack trim: the comparison basis strings live once per window now. prev_basis on
        # any window with comparisons; typical_basis only where a vs-typical read exists.
        prev_basis=(
            f"the {n} days immediately before this window" if with_comparisons else None
        ),
        typical_basis=(_TYPICAL_BASIS[n] if (with_comparisons and with_typical) else None),
    )


def build_recent_training(
    facts: List[Any], as_of: date, *, include_previous_30d: bool = True
) -> RecentTrainingContext:
    """Build the modality-aware recent-training picture as of `as_of` from facts
    spanning at least the trailing ~200 days (the 30d window plus its ~6-month
    vs-typical baseline). Facts are duck-typed `ActivityFact`s: each needs
    `local_date`, `activity_type`, `distance_m`, `moving_time_s`, `effort_score`,
    and (for the per-activity list) `effort`.

    `include_previous_30d` is the pure seam for the #522 COACH_PREVIOUS_30D_ENABLED
    kill switch: when False the previous_30d window is omitted (None, dropped from
    serialization). The vs-prev comparisons on last_7d/last_30d are computed from the
    facts directly and are unaffected."""
    # 7d: vs-prev only (no vs-typical — training_volume owns the weekly vs-norm, #451).
    last_7d = _window(
        "last_7d", 7, as_of - timedelta(days=6), as_of, facts,
        as_of=as_of, with_comparisons=True, with_activities=True, with_typical=False,
    )
    # 30d: both vs-typical (the ~6-month per-day rate) and vs-prev.
    last_30d = _window(
        "last_30d", 30, as_of - timedelta(days=29), as_of, facts,
        as_of=as_of, with_comparisons=True, with_activities=False, with_typical=True,
    )
    previous_30d = (
        _window(
            "previous_30d", 30, as_of - timedelta(days=59), as_of - timedelta(days=30), facts,
            as_of=as_of, with_comparisons=False, with_activities=False,
        )
        if include_previous_30d
        else None
    )

    # has_baseline mirrors the vs-typical abstention: true when a 30d metric resolved a
    # norm (build_volume_report sets direction 'no_norm' when too thin). Only the 30d
    # window carries vs-typical now (#451); the 7d rows carry a null vs-typical direction
    # (pack trim), so a resolved norm is read off the 30d window alone.
    has_baseline = any(
        c.vs_typical_direction not in (None, "no_norm")
        for c in last_30d.comparisons
    )

    return RecentTrainingContext(
        last_7d=last_7d,
        last_30d=last_30d,
        previous_30d=previous_30d,
        has_baseline=has_baseline,
    )
