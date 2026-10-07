"""What "typical" means for this runner's RUNNING (#830).

The volume builder's norm is all-activity by design, and for a runner who walks
30-35 km a week that is roughly three times their actual running. Both numbers
are true and neither is wrong, but stacking a running-km headline on top of an
all-activity gauge puts two different quantities in the same glance and invites
the reader to compare them.

So the running-only norm gets one definition, here, used by BOTH the week read
(the free-mode gauge) and the drafting context (the figure a running plan is
built against). It is the same clamped per-day rate as everywhere else — the
`activity_facts` primitives do the arithmetic — so "typical" still means one
thing across the product.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, List, Optional, Sequence

from app.services.activity_facts import (
    BASELINE_WEEKS,
    DEADBAND_PCT,
    MIN_BASELINE_ACTIVITIES,
    baseline_window,
    direction,
    is_run,
    norm_per_day,
    sum_metric,
)
from app.services.schedule.disciplines import discipline_for_fact


def running_norm_weekly_m(
    facts: Sequence[Any], as_of: date, *, weeks: int = BASELINE_WEEKS
) -> Optional[float]:
    """The runner's own typical weekly RUNNING distance, or None.

    Computed over history BEFORE the current 7 days, like every other norm here,
    so this week's training cannot move the line it is being measured against.
    Abstains when there is too little history to say — a runner with no baseline
    is exactly the person a made-up figure would serve worst.
    """
    baseline_end = as_of - timedelta(days=7)
    window = baseline_window(facts, baseline_end, weeks * 7)
    if window is None:
        return None
    start, end, days = window
    runs = [f for f in facts if is_run(f)]
    per_day = norm_per_day(runs, start, end, days)
    distance = per_day.get("distance_m")
    return distance * 7 if distance is not None else None


@dataclass(frozen=True)
class DisciplineNorm:
    """One activity's share of the runner's typical week (#1044)."""

    discipline: str
    sessions: float
    moving_time_s: float
    distance_m: float


def weekly_norms_by_discipline(
    facts: Sequence[Any], as_of: date, *, weeks: int = BASELINE_WEEKS
) -> List[DisciplineNorm]:
    """The runner's typical week split by activity, largest time first, or [].

    Same window and calendar-day denominator as `running_norm_weekly_m`. The
    minimum-history gate applies to the whole window, not to each activity, so a
    rare activity still shows its small share instead of vanishing.
    """
    baseline_end = as_of - timedelta(days=7)
    window = baseline_window(list(facts), baseline_end, weeks * 7)
    if window is None:
        return []
    start, end, days = window
    in_window = [f for f in facts if start <= f.local_date <= end]
    if len(in_window) < MIN_BASELINE_ACTIVITIES:
        return []
    by_discipline: dict = {}
    for fact in in_window:
        by_discipline.setdefault(discipline_for_fact(fact), []).append(fact)
    scale = 7 / days
    norms = [
        DisciplineNorm(
            discipline=discipline,
            sessions=len(group) * scale,
            moving_time_s=sum_metric(group, "moving_time_s") * scale,
            distance_m=sum_metric(group, "distance_m") * scale,
        )
        for discipline, group in by_discipline.items()
    ]
    return sorted(norms, key=lambda n: n.moving_time_s, reverse=True)


def weekly_hours_norm_s(facts: Sequence[Any], as_of: date) -> Optional[float]:
    """The runner's typical weekly moving time across every activity, or None."""
    norms = weekly_norms_by_discipline(facts, as_of)
    return sum(n.moving_time_s for n in norms) if norms else None


def running_vs_norm(
    facts: Sequence[Any],
    week_facts: Sequence[Any],
    as_of: date,
    *,
    days_elapsed: int = 7,
) -> Optional[dict]:
    """This week's running against the runner's own typical week.

    `days_elapsed` pro-rates the norm for a week still in progress, so a Tuesday
    is judged against two days of typical rather than seven — the same fairness
    the calendar-week volume read already applies.
    """
    norm = running_norm_weekly_m(facts, as_of)
    if not norm:
        return None
    current = sum_metric(list(week_facts), "distance_m", runs_only=True)
    factor = max(days_elapsed, 1) / 7
    comparable = norm * factor
    pct = round((current - comparable) / comparable * 100.0, 1) if comparable else 0.0
    return {
        "typical_weekly_distance_m": round(norm, 1),
        "current_distance_m": round(current, 1),
        "pct_vs_norm": pct,
        "direction": direction(current, norm, factor),
        "deadband_pct": DEADBAND_PCT,
    }
