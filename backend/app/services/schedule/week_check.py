"""One check of a week against every goal, collecting every failure (#1064).

A week is held to the same few questions whatever the goal that raised them: does
it reach the challenge's threshold, does it keep the runner's usual walking, does
it hold the day of each dated goal. They are asked of the frame (`frames.py`), not
of the goal, so a new kind of goal needs no new check: its demands are already
numbers on the week.

Planned zone time is an ESTIMATE (duration x the runner's own share for that
activity); the time already done in the current week is MEASURED. The two are
named apart here and in every sentence this module writes, because a plan can only
promise the first and a challenge is only won by the second.

`check_week` returns EVERY failure. A check that stopped at the first would cost
one retry per defect, and there is one retry. Each failure carries the exact gap in
the metric's own unit and a sentence the coach can act on, so the retry says "add
1.4 h" rather than "too little".

Pure: no I/O, no model.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from app.schemas.season import ChallengeRule
from app.services.schedule.challenge import spec_of
from app.services.schedule.planned_distance import planned_distance_m

if TYPE_CHECKING:  # frames imports the validator, which imports this module
    from app.services.schedule.frames import ChallengeFrame, WeekFrame

# Failure codes. A caller acts on the NUMERIC ones differently (repair may close
# them), so the vocabulary is closed and small.
CHALLENGE = "challenge"
WALKING = "walking_floor"
DATED_GOAL = "dated_goal"

NUMERIC_CODES = frozenset({CHALLENGE, WALKING})

# A goal day must hold most of the goal's distance; a few hundred metres under on
# a 10 km is a route, not a different event.
GOAL_DAY_SHARE = 0.9

# A plan is a prescription, not a stopwatch: a week within this much of its
# threshold, in the metric's own terms, meets it. A minute, ten metres. The
# schedule screen judges a planned week by the same figures (`PLAN_TOLERANCE` in
# frontend/components/schedule/challenge.ts, pinned to this by a test).
_TOLERANCE = {
    "zone_time_s": 60.0,
    "time_s": 60.0,
    "distance_m": 10.0,
    "sessions": 0.0,
}


@dataclass(frozen=True)
class WeekFailure:
    code: str
    week_start: date
    # The exact shortfall in the metric's own unit (seconds, metres, sessions);
    # 0.0 for a failure that is not a quantity (a missing goal day).
    gap: float
    message: str
    # The same failure in the RUNNER's words, for the plan's stored shortfalls:
    # `message` is written to be fed back to the coach and tells it what to add.
    shortfall: str = ""


@dataclass
class PlannedMetrics:
    """What one week's COMMITTED sessions come to, plus what was already done."""

    week_start: date
    time_s: Dict[str, float] = field(default_factory=dict)
    distance_m: Dict[str, float] = field(default_factory=dict)
    sessions_by_discipline: Dict[str, int] = field(default_factory=dict)
    # min_zone -> discipline -> seconds, ESTIMATED from the runner's zone shares.
    zone_est_s: Dict[int, Dict[str, float]] = field(default_factory=dict)
    # Measured earlier in the current week; None for any other week.
    done: Optional[Any] = None

    @property
    def total_time_s(self) -> float:
        return sum(self.time_s.values())

    def done_zone_s(self, min_zone: int, disciplines: Sequence[str] = ()) -> float:
        return self.done.zone_time_s(min_zone, disciplines) if self.done is not None else 0.0


def counts_towards_week(session: Any, today: date) -> bool:
    """Whether a session is still AHEAD and committed.

    A session whose window has closed is history: the runner's measured actuals
    already hold whatever they did, and counting its plan too would count it
    twice. The same holds for one ticked off while its window is still open (a
    floating session done on Tuesday): the actual run is in `frame.done`, so the
    plan's row for it must not count a second time. A suggestion is an offer, not
    a commitment; a rest day is not work; a dismissed session was declined.
    """
    if getattr(session, "commitment", "committed") != "committed":
        return False
    if session.intent == "rest":
        return False
    if getattr(session, "dismissed_at", None) is not None:
        return False
    completed_at = getattr(session, "completed_at", None)
    if completed_at is not None and _day_of(completed_at) < today:
        # Done before today: `frame.done` (which runs through yesterday) holds
        # it. One done TODAY is not in `frame.done` yet, so its plan still counts.
        return False
    return session.window_end >= today


def _day_of(moment: Any) -> date:
    return moment.date() if hasattr(moment, "date") and callable(moment.date) else moment


def planned_metrics(sessions: Sequence[Any], frame: "WeekFrame") -> PlannedMetrics:
    """The week's committed, still-ahead sessions as numbers.

    Estimated zone time is each session's duration times the runner's share of
    that activity at or above each zone the frame carries; an activity with no
    measured share contributes none, never a population figure.
    """
    metrics = PlannedMetrics(week_start=frame.week_start, done=frame.done)
    for zone in frame.shares:
        metrics.zone_est_s[zone] = {}
    for session in sessions:
        if not counts_towards_week(session, frame.today):
            continue
        discipline = session.discipline
        seconds = float(session.target_duration_s or 0)
        metrics.time_s[discipline] = metrics.time_s.get(discipline, 0.0) + seconds
        metrics.distance_m[discipline] = (
            metrics.distance_m.get(discipline, 0.0) + planned_distance_m(session)
        )
        metrics.sessions_by_discipline[discipline] = (
            metrics.sessions_by_discipline.get(discipline, 0) + 1
        )
        for zone in frame.shares:
            metrics.zone_est_s[zone][discipline] = metrics.zone_est_s[zone].get(
                discipline, 0.0
            ) + seconds * frame.share(zone, discipline)
    return metrics


def rule_value(rule: Any, metrics: PlannedMetrics) -> float:
    """The week's value of a rule's metric: planned (estimated) plus done
    (measured), over the rule's activities (empty = all)."""
    spec = spec_of(rule)
    wanted = set(spec.disciplines)

    def pick(by_discipline: Dict[str, float]) -> float:
        return sum(v for d, v in by_discipline.items() if not wanted or d in wanted)

    done = metrics.done
    if spec.metric == "zone_time_s":
        zone = spec.min_zone or 1
        planned = pick(metrics.zone_est_s.get(zone, {}))
        return planned + metrics.done_zone_s(zone, spec.disciplines)
    if spec.metric == "time_s":
        return pick(metrics.time_s) + (
            sum(v for d, v in done.time_s.items() if not wanted or d in wanted)
            if done is not None
            else 0.0
        )
    if spec.metric == "distance_m":
        return pick(metrics.distance_m) + (
            sum(v for d, v in done.distance_m.items() if not wanted or d in wanted)
            if done is not None
            else 0.0
        )
    return pick({d: float(n) for d, n in metrics.sessions_by_discipline.items()}) + (
        sum(
            n
            for d, n in done.sessions_by_discipline.items()
            if not wanted or d in wanted
        )
        if done is not None
        else 0.0
    )


# --- sentences ---------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def says_week(line: str, week_start: date) -> bool:
    """Whether a stored shortfall line is about the week starting `week_start`.

    Every shortfall a week's check or a shape writes names its week as "the week
    of 19 Oct", so a plan's stored shortfalls can be restated week by week
    (an amendment replaces the lines of the weeks it rewrote) without a second
    structure that could disagree with the sentences runners read.
    """
    return re.search(rf"the week of {re.escape(_day(week_start))}\b", line, re.I) is not None


def _fmt(metric: str, value: float) -> str:
    if metric in ("zone_time_s", "time_s"):
        return f"{value / 3600:.1f} h"
    if metric == "distance_m":
        return f"{value / 1000:.1f} km"
    return f"{value:.0f}"


def best_share_discipline(frame: "WeekFrame", c: "ChallengeFrame") -> Optional[str]:
    """The activity that closes a zone gap fastest for this runner: the highest
    share among those the rule allows, preferring one they already do."""
    rule = c.rule
    allowed = set(rule.disciplines)
    zone = rule.min_zone or 1
    shares = {
        d: s
        for d, s in frame.shares.get(zone, {}).items()
        if s > 0 and (not allowed or d in allowed) and d not in ("strength",)
    }
    if not shares:
        return None
    return max(shares, key=lambda d: (shares[d], frame.usual_time_s(d)))


def _challenge_failure(
    frame: "WeekFrame", c: "ChallengeFrame", metrics: PlannedMetrics
) -> Optional[WeekFailure]:
    rule = c.rule
    value = rule_value(rule, metrics)
    gap = rule.at_least - value
    if gap <= _TOLERANCE[rule.metric]:
        return None
    head = (
        f'Week of {_day(frame.week_start)}: the challenge "{c.name}" (week {c.index} of '
        f"{c.weeks}) needs {_fmt(rule.metric, rule.at_least)} of {_unit_phrase_short(c)}, "
        f"this week holds {_fmt(rule.metric, value)}"
    )
    parts = [head]
    if rule.metric == "zone_time_s":
        parts[0] += " (planned time counted at this runner's own zone shares, so an estimate"
        if metrics.done is not None:
            parts[0] += f"; {_fmt(rule.metric, metrics.done_zone_s(rule.min_zone or 1, rule.disciplines))} of it already done and measured"
        parts[0] += ")"
        parts.append(f"Add {_fmt(rule.metric, gap)} of {_unit_phrase_short(c)}.")
        best = best_share_discipline(frame, c)
        if best is not None:
            share = frame.share(rule.min_zone or 1, best)
            more = gap / share
            parts.append(
                f"Easy {_discipline_noun(best)} is the fastest way: "
                f"{share:.0%} of this runner's {best} is {_zone(rule)}, so about "
                f"{_fmt('time_s', more)} more of it closes the gap."
            )
    else:
        parts.append(f"Add {_fmt(rule.metric, gap)}.")
    parts.append(
        "Keep the rest of the week and the days around any dated goal as they are."
    )
    estimated = " (estimated from your heart-rate history)" if rule.metric == "zone_time_s" else ""
    return WeekFailure(
        CHALLENGE,
        frame.week_start,
        gap,
        " ".join(parts),
        shortfall=(
            f'The week of {_day(frame.week_start)} plans {_fmt(rule.metric, value)} of '
            f"{_unit_phrase_short(c)}{estimated}, under the {_fmt(rule.metric, rule.at_least)} "
            f'"{c.name}" needs.'
        ),
    )


def _zone(rule: ChallengeRule) -> str:
    return f"zone {rule.min_zone} or above"


def _unit_phrase_short(c: "ChallengeFrame") -> str:
    rule = c.rule
    where = f" ({'/'.join(rule.disciplines)} only)" if rule.disciplines else ""
    return {
        "zone_time_s": f"{_zone(rule)} time",
        "time_s": "moving time",
        "distance_m": "distance",
        "sessions": "sessions",
    }[rule.metric] + where


def _discipline_noun(discipline: str) -> str:
    return {"bike": "riding", "run": "running", "walk": "walking", "row": "rowing"}.get(
        discipline, discipline
    )


def _walking_failure(
    frame: "WeekFrame", metrics: PlannedMetrics
) -> Optional[WeekFailure]:
    if frame.walking_floor_m is None:
        return None
    needed = frame.walking_floor_m * frame.share_left
    walked = metrics.distance_m.get("walk", 0.0)
    if walked + 10 >= needed:
        return None
    partial = " for the days left in it" if frame.share_left < 1 else ""
    gap = needed - walked
    return WeekFailure(
        WALKING,
        frame.week_start,
        gap,
        f"Week of {_day(frame.week_start)}: this runner usually walks "
        f"{frame.usual_distance_m('walk') / 1000:.0f} km a week, so the week needs at "
        f"least {math.ceil(needed / 100) / 10:.1f} km of committed walking{partial}, "
        f"and holds {walked / 1000:.1f} km. Add {math.ceil(gap / 100) / 10:.1f} km of walking.",
        shortfall=(
            f"The week of {_day(frame.week_start)} plans {walked / 1000:.0f} km of walking, "
            f"under your usual {frame.usual_distance_m('walk') / 1000:.0f} km."
        ),
    )


def _is_goal_day(session: Any, day: date) -> bool:
    return session.window_start == session.window_end == day


def _goal_day_failure(frame: "WeekFrame", goal: Any, sessions: Sequence[Any]) -> Optional[WeekFailure]:
    on_day = [
        s
        for s in sessions
        if getattr(s, "commitment", "committed") == "committed"
        and s.intent != "rest"
        and _is_goal_day(s, goal.day)
        and s.discipline in ("run", "walk")
    ]
    want = (
        f"a committed run or walk pinned to {_day(goal.day)} (window start and end on "
        f"that day)"
    )
    if goal.kind == "race":
        want += ', intent "quality" or "long", with "race" in its title'
        qualifying = [
            s for s in on_day
            if s.intent in ("quality", "long") and "race" in (s.title or "").lower()
        ]
    else:
        qualifying = on_day
    if goal.distance_m:
        floor = goal.distance_m * GOAL_DAY_SHARE
        want += f" covering at least {floor / 1000:.1f} km"
        qualifying = [s for s in qualifying if planned_distance_m(s) >= floor - 1]
    if qualifying:
        return None
    return WeekFailure(
        DATED_GOAL,
        frame.week_start,
        0.0,
        f'Week of {_day(frame.week_start)}: "{goal.name}" is on {_day(goal.day)} and the '
        f"week does not hold it. It needs {want}.",
    )


def check_week(frame: "WeekFrame", sessions: Sequence[Any]) -> List[WeekFailure]:
    """Every way `sessions` fail the week `frame` describes. Empty means it passes.

    `sessions` is everything the week holds, new and kept alike (an amendment's
    surviving rows ride beside the new ones); completed or past sessions are not
    counted, their actuals being in the frame.
    """
    metrics = planned_metrics(sessions, frame)
    failures: List[WeekFailure] = []
    for c in frame.challenges:
        failure = _challenge_failure(frame, c, metrics)
        if failure is not None:
            failures.append(failure)
    walking = _walking_failure(frame, metrics)
    if walking is not None:
        failures.append(walking)
    # The goal day is judged on every session in the week, past ones included: a
    # race already run this week is held by the runner's history, not the plan.
    for goal in frame.dated:
        if goal.day < frame.today:
            continue
        failure = _goal_day_failure(frame, goal, sessions)
        if failure is not None:
            failures.append(failure)
    return failures


def has_numeric_only(failures: Sequence[WeekFailure]) -> bool:
    return bool(failures) and all(f.code in NUMERIC_CODES for f in failures)
