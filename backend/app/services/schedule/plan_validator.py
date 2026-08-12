"""The deterministic gate a drafted plan passes before it is stored (#830).

Schema coercion proves the plan is well-SHAPED. This proves it is COHERENT: that
its sessions sit where the model says they sit, that the week it wrote can
actually be arranged, and that it is not absurd. The coach-report path has the
same two layers — a Pydantic schema, then `validator.py`'s policy rules — and for
the same reason: a well-formed answer can still be a wrong one.

The distinction this module holds carefully
-------------------------------------------
It is a COHERENCE gate, not a coaching opinion. Whether four runs or five is
right for this runner is exactly the judgment the coach is for, and the North
Star is explicit that population defaults are a starting point to depart from,
not a template to apply. So there is no "10% rule" here and no cap on quality
sessions. The only volume check is an absurdity ceiling against the runner's OWN
recent norm — which exists to catch a plan that prescribes 100 km to a 20 km
runner, not to second-guess a coach who ramps deliberately. A week still only
SKETCHED gets a looser bound than next Tuesday, because a build is not
absurdity and a sketch will be rewritten into real sessions long before the
runner reaches it. It is measured against this runner's history, never a
population figure, and it abstains entirely when there is no history to measure
against.

A failure here is not a fallback. The report path can degrade to prose without a
tail; a plan cannot degrade — a schedule with an unsatisfiable week is worse than
no schedule, because the runner would act on it. So the draft is retried once
with the failures fed back, and then abandoned visibly.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Sequence

from app.services.schedule.placement import validate_session_window
from app.services.schedule.rules import check_rules
from app.services.weeks import MONDAY, week_start

# The absurdity ceiling, as a multiple of the runner's own recent weekly norm.
# Deliberately loose: a real build week can be well above typical, and a taper
# well below. This catches a plan that has lost the plot, not one that is bold.
MAX_WEEKLY_MULTIPLE = 2.0

# The same ceiling, loosened for a week that is still only a SKETCH. A build from
# 18 km/week to 38 km at peak over ten weeks is ordinary coaching, not a plan that
# has lost the plot — and a sketched week will be rewritten into real sessions
# long before the runner reaches it. Holding a week ten weeks out to the same
# bound as next Tuesday is the ceiling second-guessing the ramp, which is exactly
# what it is not for.
MAX_SKETCH_MULTIPLE = 3.0

# An absurdity floor on how many sessions may land on one day. Every other
# nonsense has a floor; without this one a week could pin all fourteen permitted
# sessions to a Tuesday unless the coach happened to write a rule against it, and
# "the model polices itself" is not a check. Deliberately high: three sessions in
# a day is a real thing this runner does (a walk, a run and a gym session), so
# this catches the impossible rather than expressing an opinion.
ABSURD_SESSIONS_PER_DAY = 4

# How far past the configured horizon a plan may reach before it is nonsense.
# The count caps in `draft_contract` bound how MANY weeks a plan holds, not how
# far out they sit, so without this a plan could put a week two years away and
# pass every gate.
HORIZON_SLACK_WEEKS = 2


@dataclass
class PlanCheck:
    ok: bool = True
    failures: List[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.failures.append(message)


@dataclass(frozen=True)
class _Placeable:
    """The shape `check_rules` needs: an id, an intent and a window."""

    id: str
    intent: str
    window_start: date
    window_end: date


@dataclass(frozen=True)
class _ImplicitRule:
    """A floor the checker applies whether or not the coach wrote it."""

    kind: str = "max_sessions_per_day"
    label: str = "no more sessions in a day than is physically sensible"
    count: int = ABSURD_SESSIONS_PER_DAY


def validate_drafted_plan(
    plan,
    *,
    today: date,
    starts_on: int = MONDAY,
    norm_weekly_running_m: Optional[float] = None,
    horizon_weeks: Optional[int] = None,
) -> PlanCheck:
    """Everything that must hold before a drafted plan reaches the store."""
    check = PlanCheck()
    current_week = week_start(today, starts_on)
    last_allowed_week = current_week + timedelta(
        days=7 * ((horizon_weeks or 0) + HORIZON_SLACK_WEEKS - 1)
    )

    def _within_horizon(week_start_date: date, label: str) -> None:
        if horizon_weeks and week_start_date > last_allowed_week:
            check.fail(
                f"{label} {week_start_date} is past the {horizon_weeks}-week horizon"
            )

    if not plan.weeks and not plan.sketch_weeks:
        check.fail("the plan contains no weeks at all")

    seen_weeks = set()
    for week in plan.weeks:
        if week.week_start != week_start(week.week_start, starts_on):
            check.fail(
                f"week {week.week_start} does not start on the runner's week boundary"
            )
        if week.week_start < current_week:
            check.fail(f"week {week.week_start} is in the past")
        if week.week_start in seen_weeks:
            check.fail(f"week {week.week_start} appears twice")
        seen_weeks.add(week.week_start)
        _within_horizon(week.week_start, "week")

        _validate_sessions(check, week, today, starts_on)
        _validate_rules_are_satisfiable(check, plan, week, starts_on)
        _validate_volume(check, week, norm_weekly_running_m)

    for sketch in plan.sketch_weeks:
        if sketch.week_start != week_start(sketch.week_start, starts_on):
            check.fail(
                f"sketched week {sketch.week_start} does not start on the runner's "
                "week boundary"
            )
        if sketch.week_start < current_week:
            check.fail(f"sketched week {sketch.week_start} is in the past")
        if sketch.week_start in seen_weeks:
            check.fail(
                f"week {sketch.week_start} is given as both concrete and sketched"
            )
        seen_weeks.add(sketch.week_start)
        _within_horizon(sketch.week_start, "sketched week")
        if (
            norm_weekly_running_m
            and sketch.target_running_distance_m
            and sketch.target_running_distance_m
            > norm_weekly_running_m * MAX_SKETCH_MULTIPLE
        ):
            check.fail(
                f"sketched week {sketch.week_start} plans "
                f"{sketch.target_running_distance_m / 1000:.0f} km of running against "
                f"a typical {norm_weekly_running_m / 1000:.0f} km"
            )

    return check


def _validate_sessions(check: PlanCheck, week, today: date, starts_on: int) -> None:
    for session in week.sessions:
        try:
            validate_session_window(
                session.window_start, session.window_end, starts_on
            )
        except ValueError as exc:
            check.fail(f"session {session.title!r}: {exc}")
            continue

        if week_start(session.window_start, starts_on) != week.week_start:
            check.fail(
                f"session {session.title!r} sits in week "
                f"{week_start(session.window_start, starts_on)}, not {week.week_start}"
            )
        if session.window_end < today:
            check.fail(
                f"session {session.title!r} is entirely in the past "
                f"({session.window_start}..{session.window_end})"
            )
        if session.intent == "rest" and (
            session.target_distance_m or session.target_duration_s
        ):
            check.fail(f"rest session {session.title!r} carries a training target")
        if session.intent != "rest" and not (
            session.target_distance_m
            or session.target_duration_s
            or session.reps_planned
        ):
            # A session with none of these cannot be sized at all, so it would
            # draw no bar and the runner would see a card with nothing on it.
            #
            # Rep structure counts as sizing. "8 x 400m off 90 seconds" is a fully
            # specified session that a coach would never also express as a total
            # distance, and demanding one rejected three real interval sessions in
            # a live draft — the requirement was wrong, not the plan. Such a
            # session prices at the runner's per-session median for the
            # discipline, which is an honest abstention rather than a total
            # invented from a warm-up multiplier nobody stated.
            check.fail(
                f"session {session.title!r} gives no distance, duration or rep "
                f"structure, so nothing can size it"
            )


def _validate_rules_are_satisfiable(
    check: PlanCheck, plan, week, starts_on: int
) -> None:
    """The plan's own rules must admit at least one legal arrangement.

    This is the check the whole rule vocabulary exists to make possible. A coach
    that writes "no quality the day before the long run" and then pins both to
    consecutive days has written a week the runner cannot follow, and it is
    caught here rather than discovered by the runner on Saturday.
    """
    placeable = [
        _Placeable(
            id=f"{week.week_start}-{index}",
            intent=session.intent,
            window_start=session.window_start,
            window_end=session.window_end,
        )
        for index, session in enumerate(week.sessions)
        if session.commitment == "committed"
    ]
    if not placeable:
        return
    # today=None: the plan is judged on its own terms, over the full stored
    # windows, not against the clock at the moment it happened to be drafted.
    # The implicit density floor rides along with the coach's own rules, so a
    # week has to be arrangeable without stacking a day absurdly high whether or
    # not the coach thought to forbid it.
    satisfiable, violations = check_rules(
        placeable, list(plan.rules) + [_ImplicitRule()], None
    )
    if not satisfiable:
        for violation in violations:
            # Named by the coach's OWN label, so it can recognise which rule it
            # wrote, but stating what that rule actually ENFORCES alongside it
            # (#844) — this text is fed back into the rewrite prompt, and a
            # label that misdescribes its own predicate would send the rewrite
            # after the wrong thing.
            check.fail(
                f"week {week.week_start} cannot satisfy its own rule "
                f"{violation['label']!r} ({violation['statement']}): "
                f"{violation['detail']}"
            )


def _validate_volume(
    check: PlanCheck, week, norm_weekly_running_m: Optional[float]
) -> None:
    """An absurdity ceiling against the runner's OWN norm, or no check at all.

    Abstains when there is no norm: a runner with no history is exactly the
    person a population figure would serve worst.
    """
    if not norm_weekly_running_m:
        return
    planned = sum(
        session.target_distance_m or 0.0
        for session in week.sessions
        if session.discipline == "run" and session.commitment == "committed"
    )
    if planned > norm_weekly_running_m * MAX_WEEKLY_MULTIPLE:
        check.fail(
            f"week {week.week_start} plans {planned / 1000:.0f} km of running "
            f"against a typical {norm_weekly_running_m / 1000:.0f} km"
        )
