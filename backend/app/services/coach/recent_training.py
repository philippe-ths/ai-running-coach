"""#444: the modality-aware recent-training picture for the coach pack.

A richer recent-training read than the four windowed all-activity sums of
`recent_training_summary`. For each of three windows — recent (last 7 days), recent
long (last 30 days) and the prior comparison window (the 30 days before that) — it
reports:

  - a per-activity-TYPE breakdown (counts + per-type distance/time/load totals), so
    a walk is never read as a run and the coach sees the full cardio picture;
  - a precomputed per-type SHARE of the window (the modality mix as a number to
    read, not a division the model has to do);
  - overall roll-up totals.

The recent (7d) window also carries a bounded, capped per-activity list (each
session's type and intensity/load), so the coach can speak to specific sessions.

For the 7d and 30d windows it carries a vs-TYPICAL and a vs-PREV comparison per
metric, with ALL percentages precomputed and a deadband direction (up/in_line/down/
no_norm), each tagged with a self-describing BASIS so the coach never cites a
comparison whose reference it does not know:

  - "typical" is the runner's own average daily rate over a trailing baseline
    (7d vs ~12 weeks, 30d vs ~6 months), projected onto the window — the SAME
    per-day-rate definition the Trends page uses, reused via `build_volume_report`,
    so "typical" has one meaning across the product (#444 decision 1).
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
from app.services.coach.volume import _METRICS, _direction, _sum, build_volume_report

# Cap on the per-activity list for the recent window (bounded by design — the longer
# windows carry only the per-type roll-up, never a per-session list).
_MAX_RECENT_ACTIVITIES = 12

# Self-describing basis for the vs-typical comparison, by window length. Mirrors the
# per-day-rate definition build_volume_report computes (12 weeks for 7d, 6 months for
# 30d), so the label and the number always agree.
_TYPICAL_BASIS = {
    7: (
        "your own average daily training over the last ~12 weeks, projected onto "
        "7 days (rest days counted as zero)"
    ),
    30: (
        "your own average daily training over the last ~6 months, projected onto "
        "30 days (rest days counted as zero)"
    ),
}


def _unknown_type(fact: Any) -> str:
    return (getattr(fact, "activity_type", None) or "unknown")


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
    facts: List[Any], as_of: date, n: int
) -> List[RecentComparison]:
    """The vs-typical (per-day-rate, reused from build_volume_report) + vs-prev
    comparison per metric for a window of `n` days ending `as_of`."""
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
                current_all=t.current_all if t else _sum(window_facts, metric),
                current_runs=t.current_runs if t else _sum(window_facts, metric, runs_only=True),
                vs_typical_pct=t.pct_vs_norm if t else None,
                vs_typical_direction=t.direction if t else "no_norm",
                vs_prev_pct=prev_pct,
                vs_prev_direction=prev_dir,
                typical_basis=_TYPICAL_BASIS[n],
                prev_basis=f"the {n} days immediately before this window",
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
        comparisons=_comparisons(facts, as_of, n) if with_comparisons else [],
        activities=_recent_activities(window_facts) if with_activities else [],
    )


def build_recent_training(facts: List[Any], as_of: date) -> RecentTrainingContext:
    """Build the modality-aware recent-training picture as of `as_of` from facts
    spanning at least the trailing ~200 days (the 30d window plus its ~6-month
    vs-typical baseline). Facts are duck-typed `ActivityFact`s: each needs
    `local_date`, `activity_type`, `distance_m`, `moving_time_s`, `effort_score`,
    and (for the per-activity list) `effort`."""
    last_7d = _window(
        "last_7d", 7, as_of - timedelta(days=6), as_of, facts,
        as_of=as_of, with_comparisons=True, with_activities=True,
    )
    last_30d = _window(
        "last_30d", 30, as_of - timedelta(days=29), as_of, facts,
        as_of=as_of, with_comparisons=True, with_activities=False,
    )
    previous_30d = _window(
        "previous_30d", 30, as_of - timedelta(days=59), as_of - timedelta(days=30), facts,
        as_of=as_of, with_comparisons=False, with_activities=False,
    )

    # has_baseline mirrors the vs-typical abstention: true when any 7d/30d metric
    # resolved a norm (build_volume_report sets direction 'no_norm' when too thin).
    has_baseline = any(
        c.vs_typical_direction != "no_norm"
        for c in (last_7d.comparisons + last_30d.comparisons)
    )

    return RecentTrainingContext(
        last_7d=last_7d,
        last_30d=last_30d,
        previous_30d=previous_30d,
        has_baseline=has_baseline,
    )
