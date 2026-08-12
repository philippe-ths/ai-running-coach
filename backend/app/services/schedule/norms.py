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

from datetime import date, timedelta
from typing import Any, Optional, Sequence

from app.services.activity_facts import (
    BASELINE_WEEKS,
    DEADBAND_PCT,
    baseline_window,
    direction,
    is_run,
    norm_per_day,
    sum_metric,
)


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
