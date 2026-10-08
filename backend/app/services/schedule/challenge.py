"""Challenge arithmetic: what the runner does now, how fast a rule can be
reached, and how a rule is going (#1064).

A challenge goal ("10 h of zone 2+ every week for 10 straight weeks") is the
coach's OPINION once, when it chooses the rule, and ARITHMETIC ever after. This
module is the arithmetic: pure functions over the fact stream, no I/O, no model.
The coach is shown `current_level` and `earliest_start` as facts to weigh; the
only hard limit on a rule is the absurdity ceiling in `season_check`.

All weekly figures come from `norms.weekly_actuals`, so a week's zone time is
what the runner's own heart-rate data recorded. Nothing here estimates.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from app.schemas.season import ChallengeMetric, ChallengeRule, ChallengeWeek
from app.services.activity_facts import BASELINE_WEEKS, RECENT_WEEKS
from app.services.schedule.norms import WeekActuals, weekly_actuals
from app.services.weeks import week_start as _week_start

# The most a week may rise on the week before when a challenge is being reached
# from the runner's current level. A FACT shown to the coach, not a gate: the
# schedule's stance (plan_validator) is that code holds absurdity ceilings and
# the ramp itself is the coach's opinion.
CHALLENGE_RAMP = 0.10

# Where a ramp starts from when the runner currently does none of the thing: a
# ramp from zero never leaves zero. Half an hour, three km or one session is a
# small week that anyone can start from, and says so rather than hiding it.
RAMP_FLOOR = {
    "zone_time_s": 1800.0,
    "time_s": 1800.0,
    "distance_m": 3000.0,
    "sessions": 1.0,
}


@dataclass(frozen=True)
class MetricSpec:
    """What a challenge counts: a metric over some disciplines (none = all)."""

    metric: ChallengeMetric
    disciplines: Tuple[str, ...] = ()
    min_zone: Optional[int] = None


SpecLike = Union[ChallengeRule, MetricSpec]


def spec_of(rule_or_spec: SpecLike) -> MetricSpec:
    return MetricSpec(
        metric=rule_or_spec.metric,
        disciplines=tuple(rule_or_spec.disciplines),
        min_zone=rule_or_spec.min_zone,
    )


def week_value(spec: SpecLike, actuals: WeekActuals) -> float:
    """One week's value of the metric, in the metric's own unit."""
    spec = spec_of(spec)
    wanted = set(spec.disciplines)

    def pick(by_discipline: Dict[str, float]) -> float:
        return sum(
            value for d, value in by_discipline.items() if not wanted or d in wanted
        )

    if spec.metric == "zone_time_s":
        return actuals.zone_time_s(spec.min_zone or 1, spec.disciplines)
    if spec.metric == "time_s":
        return pick(actuals.time_s)
    if spec.metric == "distance_m":
        return pick(actuals.distance_m)
    return pick(
        {d: float(n) for d, n in actuals.sessions_by_discipline.items()}
    )


def _complete_weeks_before(
    as_of: date, starts_on: int, count: int
) -> List[date]:
    """The `count` weeks that ended before `as_of`'s week began, oldest first."""
    current = _week_start(as_of, starts_on)
    return [current - timedelta(weeks=n) for n in range(count, 0, -1)]


def current_level(
    spec: SpecLike,
    facts: Sequence[Any],
    as_of: date,
    starts_on: int,
) -> float:
    """The runner's current weekly value of the metric.

    The higher of the last four complete weeks' mean (what they are doing now)
    and the twelve-week mean (what they can sustain). Either alone misleads: a
    lone big recent week inflates the first, a lull the second. The longer window
    is clamped to the runner's first activity, the `baseline_window` rule, so a
    short history is not diluted by weeks they had not started training.
    """
    facts = list(facts)
    if not facts:
        return 0.0
    first_week = _week_start(min(f.local_date for f in facts), starts_on)

    def mean_over(count: int) -> float:
        weeks = [w for w in _complete_weeks_before(as_of, starts_on, count) if w >= first_week]
        if not weeks:
            return 0.0
        return sum(week_value(spec, weekly_actuals(facts, w, starts_on)) for w in weeks) / len(weeks)

    return max(mean_over(RECENT_WEEKS), mean_over(BASELINE_WEEKS))


def weeks_to_reach(spec: SpecLike, at_least: float, current: float) -> int:
    """Whole weeks of a `CHALLENGE_RAMP` rise (a metric of sessions: one more a
    week) needed to climb from `current` to `at_least`; 0 when already there."""
    spec = spec_of(spec)
    if current >= at_least:
        return 0
    if spec.metric == "sessions":
        return math.ceil(at_least - current)
    base = max(current, RAMP_FLOOR[spec.metric])
    if base >= at_least:
        return 1
    return math.ceil(math.log(at_least / base) / math.log(1 + CHALLENGE_RAMP))


def earliest_start(
    rule_or_spec: SpecLike,
    current: float,
    as_of: date,
    starts_on: int,
    at_least: Optional[float] = None,
) -> date:
    """The first week start from which a ramp of at most `CHALLENGE_RAMP` a week
    from `current` reaches `at_least` in the first covered week.

    The current week is partly gone, so it is not counted as a ramp step: the
    ramp's first rise lands next week. A runner already at the level starts this
    week. `at_least` comes from the rule when one is passed.

    A runner at zero ramps from `RAMP_FLOOR`, a small week anyone can start, since
    a percentage of nothing is nothing.
    """
    if at_least is None:
        at_least = rule_or_spec.at_least  # type: ignore[union-attr]
    steps = weeks_to_reach(rule_or_spec, at_least, current)
    return _week_start(as_of, starts_on) + timedelta(weeks=steps)


def challenge_status(
    rule: ChallengeRule,
    facts: Sequence[Any],
    today: date,
    starts_on: int,
    planned_by_week: Optional[Dict[date, float]] = None,
) -> Tuple[List[ChallengeWeek], int]:
    """Each covered week against the rule, and the streak of met weeks.

    A week that has ended shows what was measured and whether it met the rule. The
    current week shows what has happened so far with `met` left None, because a
    week still being lived has not failed yet. Later weeks show only the
    threshold (and the plan's figure when `planned_by_week` has one).

    The streak is the run of consecutive MET weeks from the first covered week; it
    stops at the first completed week that missed, and never counts a week still
    under way.
    """
    this_week = _week_start(today, starts_on)
    weeks: List[ChallengeWeek] = []
    streak = 0
    streak_alive = True
    for index in range(rule.weeks):
        start = rule.start + timedelta(weeks=index)
        planned = (planned_by_week or {}).get(start)
        actual: Optional[float] = None
        met: Optional[bool] = None
        if start <= this_week:
            actual = week_value(rule, weekly_actuals(facts, start, starts_on))
            if start < this_week:
                met = actual >= rule.at_least
        weeks.append(
            ChallengeWeek(
                week_start=start,
                index=index + 1,
                threshold=rule.at_least,
                planned=planned,
                actual=actual,
                met=met,
            )
        )
        if streak_alive and met:
            streak += 1
        else:
            streak_alive = False
    return weeks, streak
