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

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

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
from app.services.weeks import week_start as _week_start


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


# Under an hour of heart-rate-measured time in the baseline, an activity's zone
# share is noise; it counts as no zone time rather than borrowing a population
# figure.
MIN_ZONE_MEASURED_S = 3600


def _zone_number(key: Any) -> Optional[int]:
    """"Z2" -> 2. `time_in_zones` keys are "Z1".."Z5"; anything else is skipped."""
    try:
        return int(str(key).lstrip("Zz"))
    except ValueError:
        return None


def zone_seconds(fact: Any) -> Dict[int, float]:
    """One activity's time in each heart-rate zone, in seconds of MOVING time.

    The stored `time_in_zones` counts heart-rate SAMPLES, which equal seconds
    only for a 1 s recording. A watch on smart recording logs a walk or a ride
    about every 4 s, so its stored figures run about 4x short. Each zone's share
    of the samples is spread over the activity's moving time instead, so a walk
    and a run are measured on the same clock. Empty when there is no heart-rate
    data: that time is never guessed.
    """
    counts = {}
    for key, value in (getattr(fact, "time_in_zones", None) or {}).items():
        number = _zone_number(key)
        if number is not None and value:
            counts[number] = counts.get(number, 0) + value
    total = sum(counts.values())
    moving = getattr(fact, "moving_time_s", 0) or 0
    if total <= 0 or moving <= 0:
        return {}
    return {number: moving * value / total for number, value in counts.items()}


def zone_shares(
    facts: Sequence[Any], as_of: date, min_zone: int, *, weeks: int = BASELINE_WEEKS
) -> dict:
    """Per discipline, the share of this runner's heart-rate time spent in zone
    `min_zone` or higher, over the same baseline window as the other norms.

    Only disciplines with at least `MIN_ZONE_MEASURED_S` measured are present; a
    planned session of any other discipline is estimated at no zone time.
    """
    baseline_end = as_of - timedelta(days=7)
    window = baseline_window(list(facts), baseline_end, weeks * 7)
    if window is None:
        return {}
    start, end, _ = window
    measured: dict = {}
    in_zone: dict = {}
    for fact in facts:
        if not (start <= fact.local_date <= end):
            continue
        zones = zone_seconds(fact)
        if not zones:
            continue
        discipline = discipline_for_fact(fact)
        for number, seconds in zones.items():
            measured[discipline] = measured.get(discipline, 0) + seconds
            if number >= min_zone:
                in_zone[discipline] = in_zone.get(discipline, 0) + seconds
    return {
        discipline: in_zone.get(discipline, 0) / total
        for discipline, total in measured.items()
        if total >= MIN_ZONE_MEASURED_S
    }


def estimated_zone_s(seconds_by_discipline: dict, shares: dict) -> float:
    """A week's estimated time in the zone: each activity's time x its share."""
    return sum(
        (seconds or 0) * shares.get(discipline, 0.0)
        for discipline, seconds in seconds_by_discipline.items()
    )


@dataclass
class WeekActuals:
    """One week of what the runner actually did, measured and never estimated.

    `zone_s[discipline][n]` is the seconds spent in zone n, from the stored
    `time_in_zones` spread over moving time (`zone_seconds`). An activity with no heart-rate data adds nothing
    there: a challenge counted from a guess would let a week "pass" on an
    estimate the runner's own watch never recorded.
    """

    week_start: date
    time_s: Dict[str, float] = field(default_factory=dict)
    distance_m: Dict[str, float] = field(default_factory=dict)
    sessions_by_discipline: Dict[str, int] = field(default_factory=dict)
    zone_s: Dict[str, Dict[int, float]] = field(default_factory=dict)

    @property
    def sessions(self) -> int:
        return sum(self.sessions_by_discipline.values())

    @property
    def total_time_s(self) -> float:
        return sum(self.time_s.values())

    def zone_time_s(
        self, min_zone: int, disciplines: Iterable[str] = ()
    ) -> float:
        """Measured seconds in `min_zone` or higher; every discipline when none named."""
        wanted = set(disciplines)
        return sum(
            seconds
            for discipline, by_zone in self.zone_s.items()
            if not wanted or discipline in wanted
            for number, seconds in by_zone.items()
            if number >= min_zone
        )


def weekly_actuals(
    facts: Sequence[Any],
    week_start: date,
    starts_on: int,
    *,
    through: Optional[date] = None,
) -> WeekActuals:
    """The totals for the week containing `week_start` (snapped to the runner's
    own boundary): time and distance by discipline, session count, measured zone
    time.

    `through` stops the count at that day, inclusive. A challenge week still
    being lived counts what was done before today plus what is planned from
    today, and counting today twice (done AND planned) would let a week pass on
    a session counted two ways.
    """
    start = _week_start(week_start, starts_on)
    end = start + timedelta(days=6)
    if through is not None:
        end = min(end, through)
    actuals = WeekActuals(week_start=start)
    for fact in facts:
        if not (start <= fact.local_date <= end):
            continue
        discipline = discipline_for_fact(fact)
        actuals.time_s[discipline] = (
            actuals.time_s.get(discipline, 0) + (fact.moving_time_s or 0)
        )
        actuals.distance_m[discipline] = (
            actuals.distance_m.get(discipline, 0) + (fact.distance_m or 0)
        )
        actuals.sessions_by_discipline[discipline] = (
            actuals.sessions_by_discipline.get(discipline, 0) + 1
        )
        for number, seconds in zone_seconds(fact).items():
            by_zone = actuals.zone_s.setdefault(discipline, {})
            by_zone[number] = by_zone.get(number, 0) + seconds
    return actuals


def walking_norm_weekly_m(facts: Sequence[Any], as_of: date) -> Optional[float]:
    """The runner's typical weekly walking distance, or None."""
    walk = next(
        (n for n in weekly_norms_by_discipline(facts, as_of) if n.discipline == "walk"),
        None,
    )
    return walk.distance_m if walk is not None else None


# A session is "typical" for repair only from a run of history that says so; a
# discipline done fewer times than this in the baseline window has no typical.
MIN_TYPICAL_SESSIONS = 3
# Under a quarter hour is a warm-up or a stray tracker blip, not a session.
MIN_TYPICAL_SESSION_S = 900


def typical_sessions(
    facts: Sequence[Any], as_of: date, *, weeks: int = BASELINE_WEEKS
) -> Dict[str, "TypicalSession"]:
    """Per discipline, the median session this runner actually does.

    Repair copies these instead of inventing a session: the duration and distance
    are the runner's own, so the pace a copy implies is their pace. A discipline
    with fewer than `MIN_TYPICAL_SESSIONS` sessions in the window has no entry.
    """
    baseline_end = as_of - timedelta(days=7)
    window = baseline_window(list(facts), baseline_end, weeks * 7)
    if window is None:
        return {}
    start, end, _ = window
    by_discipline: Dict[str, List[Any]] = {}
    for fact in facts:
        if not (start <= fact.local_date <= end):
            continue
        if (fact.moving_time_s or 0) < MIN_TYPICAL_SESSION_S:
            continue
        by_discipline.setdefault(discipline_for_fact(fact), []).append(fact)

    def median(values: List[float]) -> float:
        ordered = sorted(values)
        mid = len(ordered) // 2
        return (
            ordered[mid]
            if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2
        )

    out: Dict[str, TypicalSession] = {}
    for discipline, group in by_discipline.items():
        if len(group) < MIN_TYPICAL_SESSIONS:
            continue
        with_distance = [f.distance_m for f in group if (f.distance_m or 0) > 0]
        out[discipline] = TypicalSession(
            discipline=discipline,
            duration_s=median([float(f.moving_time_s) for f in group]),
            # A median over the sessions that HAVE a distance, so a gym session
            # logged with none does not drag a ride's distance to zero.
            distance_m=median(with_distance)
            if len(with_distance) >= MIN_TYPICAL_SESSIONS
            else None,
        )
    return out


@dataclass(frozen=True)
class TypicalSession:
    discipline: str
    duration_s: float
    distance_m: Optional[float] = None


def longest_recent_run_m(
    facts: Sequence[Any], as_of: date, *, weeks: int = 8
) -> Optional[float]:
    """The runner's longest single run in the last `weeks` weeks: where a long-run
    target starts from. None when they have run none."""
    cutoff = as_of - timedelta(weeks=weeks)
    runs = [
        f.distance_m
        for f in facts
        if is_run(f) and cutoff <= f.local_date <= as_of and (f.distance_m or 0) > 0
    ]
    return max(runs) if runs else None


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
