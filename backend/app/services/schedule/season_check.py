"""The deterministic gate a season passes before it is stored (#1064).

The season is the coach's OPINION: what each goal is, when to do it, how the
months are spent. This module never disagrees with an opinion. It checks the
things that are not opinions: every goal the runner wrote is answered exactly
once, a booked date is where the runner put it, nothing is dated in the past, the
timeline has no holes, and a challenge is not absurd for the runner doing it.

The challenge check follows the schedule's one stance on volume
(`plan_validator`'s docstring): code holds absurdity ceilings, never a "10 %
rule". How fast to ramp is the coach's call, so the ramp arithmetic is shown to
the coach as a fact (`challenge.earliest_start`) and the only hard limit is a
multiple of the runner's own current level, the same multiples the weekly gate
uses.

`check_season` returns EVERY failure, each a sentence the coach can act on. A
check that stopped at the first would cost one retry per defect, and there is
only one retry.
"""

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Sequence

from app.core.config import settings
from app.schemas.season import ChallengeRule, GoalView, SeasonPlan
from app.services.schedule import challenge
from app.services.schedule.plan_validator import (
    MAX_WEEKLY_MULTIPLE,
)
from app.services.weeks import week_start as _week_start

# A kind with a day or a window to recommend. The owner wants a recommendation
# for each of these, so a view without one is a goal the coach left unanswered.
_DATED_KINDS = ("race", "finish", "completion")

LevelFor = Callable[[ChallengeRule], float]


def _fmt_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def _fmt_value(metric: str, value: float) -> str:
    if metric in ("zone_time_s", "time_s"):
        return f"{value / 3600:.1f} h"
    if metric == "distance_m":
        return f"{value / 1000:.1f} km"
    return f"{value:.0f} sessions" if value != 1 else "1 session"


def describe_rule(rule: ChallengeRule) -> str:
    """A rule in the runner's words, for the failure messages and the prompt."""
    what = {
        "zone_time_s": f"time in zone {rule.min_zone} or above",
        "time_s": "time",
        "distance_m": "distance",
        "sessions": "sessions",
    }[rule.metric]
    where = f" ({', '.join(rule.disciplines)} only)" if rule.disciplines else ""
    return f"{what}{where}"


def _view_day(view: GoalView):
    """The latest day a view puts the goal on: its date, else its window's end."""
    return view.date or view.window_end


def _challenge_failures(
    name: str,
    rule: ChallengeRule,
    *,
    today: date,
    starts_on: int,
    level_for: LevelFor,
) -> List[str]:
    out: List[str] = []
    current_week = _week_start(today, starts_on)
    boundary = "Sunday" if starts_on == 6 else "Monday"
    if _week_start(rule.start, starts_on) != rule.start:
        out.append(
            f'The challenge "{name}" starts on {_fmt_date(rule.start)}, which is not a '
            f"week boundary. This runner's weeks begin on {boundary}; start it on one "
            f"({_fmt_date(_week_start(rule.start, starts_on))} is the nearest)."
        )
    if rule.start < current_week:
        out.append(
            f'The challenge "{name}" starts on {_fmt_date(rule.start)}, before the '
            f"current week ({_fmt_date(current_week)})."
        )
        return out
    level = level_for(rule)
    spec = challenge.spec_of(rule)
    # A runner doing none of it is measured against the ramp's floor, so a
    # ceiling of 2 x 0 does not forbid every challenge outright.
    reference = max(level, challenge.RAMP_FLOOR[rule.metric])
    weeks_out = (_week_start(rule.start, starts_on) - current_week).days // 7
    if weeks_out >= settings.SCHEDULE_CONCRETE_WEEKS:
        # A later start leaves weeks the coach builds across, and each of those
        # weeks is held to the plan's own absurdity ceilings as it is written. A
        # level ceiling here would only second-guess the ramp, which is the
        # coach's call.
        return out
    multiple = MAX_WEEKLY_MULTIPLE
    ceiling = reference * multiple
    if rule.at_least > ceiling:
        earliest = challenge.earliest_start(
            spec, level, today, starts_on, at_least=rule.at_least
        )
        when = (
            f"a start from the week of {_fmt_date(earliest)} lets a ramp of "
            f"{challenge.CHALLENGE_RAMP:.0%} a week get there"
        )
        out.append(
            f'The challenge "{name}" asks for {_fmt_value(rule.metric, rule.at_least)} '
            f"of {describe_rule(rule)} a week from the week of {_fmt_date(rule.start)}, "
            f"but this runner currently does {_fmt_value(rule.metric, level)} a week and "
            f"a start within {settings.SCHEDULE_CONCRETE_WEEKS} weeks "
            f"may ask at most {multiple:g}x that, {_fmt_value(rule.metric, ceiling)}. "
            f"Start later (for scale, {when}) or ask for less."
        )
    return out


def check_season(
    plan: SeasonPlan,
    goals: Sequence[Any],
    today: date,
    starts_on: int,
    levels: LevelFor,
) -> List[str]:
    """Every way `plan` fails, as sentences. Empty means it passes.

    `goals` are the runner's UPCOMING goals (`store.list_goal_races` with
    `on_or_after`). `levels` maps a challenge rule to the runner's current weekly
    value of what it counts, so this module stays free of any fact stream.
    """
    failures: List[str] = []
    by_id = {goal.id: goal for goal in goals}
    current_week = _week_start(today, starts_on)

    # --- every goal answered once -------------------------------------------
    seen: Dict[Any, int] = {}
    for view in plan.goals:
        seen[view.goal_id] = seen.get(view.goal_id, 0) + 1
    for goal_id, count in seen.items():
        if goal_id not in by_id:
            failures.append(
                f"The season has a view for goal id {goal_id}, which is not one of this "
                "runner's goals. Answer only the goals listed, by their ids."
            )
        elif count > 1:
            failures.append(
                f'The season answers "{by_id[goal_id].name}" {count} times. '
                "Give each goal exactly one view."
            )
    for goal in goals:
        if goal.id not in seen:
            failures.append(
                f'The season has no view for "{goal.name}" (id {goal.id}). '
                "Every goal the runner wrote needs one."
            )

    # --- each view against the goal it answers ------------------------------
    for view in plan.goals:
        goal = by_id.get(view.goal_id)
        if goal is None:
            continue
        name = goal.name
        if goal.booked and goal.race_date is not None and view.date != goal.race_date:
            got = _fmt_date(view.date) if view.date else "no exact date"
            failures.append(
                f'"{name}" is booked for {_fmt_date(goal.race_date)}, but the season '
                f"gives {got}. A booked date never moves: set the date to "
                f"{goal.race_date.isoformat()}."
            )
        if view.kind in _DATED_KINDS and not (view.date or view.window_start):
            failures.append(
                f'"{name}" is a {view.kind} goal with no date or window. Recommend '
                "one, even if only a window."
            )
        if view.date is not None and view.date < current_week:
            failures.append(
                f'"{name}" is dated {_fmt_date(view.date)}, before the current week '
                f"({_fmt_date(current_week)})."
            )
        if view.window_end is not None and view.window_end < current_week:
            failures.append(
                f'"{name}" has a window ending {_fmt_date(view.window_end)}, before the '
                f"current week ({_fmt_date(current_week)})."
            )
        for event in view.events:
            event_end = event.date or event.window_end
            if event_end is not None and event_end < today:
                failures.append(
                    f'The suggested event "{event.name}" for "{name}" is dated '
                    f"{_fmt_date(event_end)}, which has passed. Suggest only events "
                    "still ahead, or leave the date out when the source gives none."
                )
        if view.challenge is not None:
            failures.extend(
                _challenge_failures(
                    name, view.challenge, today=today, starts_on=starts_on,
                    level_for=levels,
                )
            )

    # --- the phase timeline --------------------------------------------------
    phases = list(plan.phases)
    if not phases:
        failures.append(
            "The season has no phases. Lay a timeline from this week to the last "
            "dated goal."
        )
        return failures
    ordered = sorted(phases, key=lambda p: (p.start, p.end))
    if [id(p) for p in ordered] != [id(p) for p in phases]:
        failures.append("The phases are not in date order. List them earliest first.")
    for phase in ordered:
        if phase.goal_id is not None and phase.goal_id not in by_id:
            failures.append(
                f"A {phase.kind} phase ({phase.start.isoformat()} to "
                f"{phase.end.isoformat()}) names goal id {phase.goal_id}, which is not "
                "one of this runner's goals."
            )
    for previous, phase in zip(ordered, ordered[1:]):
        expected = previous.end + timedelta(days=1)
        if phase.start < expected:
            failures.append(
                f"The {previous.kind} phase ({previous.start.isoformat()} to "
                f"{previous.end.isoformat()}) overlaps the {phase.kind} phase that "
                f"starts {phase.start.isoformat()}. Phases must not overlap."
            )
        elif phase.start > expected:
            failures.append(
                f"There is a gap between the {previous.kind} phase ending "
                f"{previous.end.isoformat()} and the {phase.kind} phase starting "
                f"{phase.start.isoformat()}. Each phase must start the day after the "
                f"previous one ends ({expected.isoformat()})."
            )
    first, last = ordered[0], ordered[-1]
    if not (first.start <= today <= first.end):
        failures.append(
            f"The first phase runs {first.start.isoformat()} to {first.end.isoformat()} "
            f"but today is {today.isoformat()}. The timeline must begin by covering today."
        )
    dated = [
        (by_id[v.goal_id].name, _view_day(v))
        for v in plan.goals
        if v.goal_id in by_id and v.kind in _DATED_KINDS and _view_day(v) is not None
    ]
    if dated:
        name, latest = max(dated, key=lambda item: item[1])
        if last.end < latest:
            failures.append(
                f"The timeline ends {last.end.isoformat()} but \"{name}\" falls on "
                f"{latest.isoformat()}. Extend the phases to at least that day."
            )
    for view in plan.goals:
        goal = by_id.get(view.goal_id)
        if goal is None or view.challenge is None:
            continue
        # A challenge week with no phase is a week no frame can state: it would be
        # neither written nor sketched, and the rule would be unplanned there.
        covered_to = view.challenge.last_week_start + timedelta(days=6)
        if last.end < covered_to:
            failures.append(
                f'The timeline ends {last.end.isoformat()} but the challenge '
                f'"{goal.name}" runs through the week of '
                f'{_fmt_date(view.challenge.last_week_start)} (to {covered_to.isoformat()}). '
                "Extend the phases to at least that day, so every week of the challenge "
                "has a phase."
            )
    for view in plan.goals:
        goal = by_id.get(view.goal_id)
        if goal is None or view.kind not in _DATED_KINDS or view.date is None:
            continue
        holders = [
            p for p in ordered
            if p.kind == "race" and p.goal_id == view.goal_id and p.start <= view.date <= p.end
        ]
        if not holders:
            failures.append(
                f'"{goal.name}" is on {_fmt_date(view.date)} but no race phase carrying '
                f"its goal id contains that day. Add a race phase for it with "
                f"goal_id {view.goal_id}."
            )
    return failures
