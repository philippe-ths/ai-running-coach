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
from app.services.schedule.planned_distance import planned_distance_m
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

# The code a volume-ceiling rejection carries, so the caller can tell a plan that
# ramps absurdly from one whose week cannot be arranged WITHOUT reading the
# failure prose. The runner is owed different advice in the two cases ("ask for a
# gentler build" against "ask again"), and matching on the message text to work
# out which is the string-matching that goes wrong the first time the wording is
# improved.
VOLUME_CEILING = "volume_ceiling"

# How far past the configured horizon a plan may reach before it is nonsense.
# The count caps in `draft_contract` bound how MANY weeks a plan holds, not how
# far out they sit, so without this a plan could put a week two years away and
# pass every gate.
HORIZON_SLACK_WEEKS = 2


def volume_ceilings(
    norm_weekly_running_m: Optional[float],
) -> Optional[tuple]:
    """The concrete and sketched running ceilings this runner's own norm implies.

    One definition, because the number is now SAID as well as enforced: the
    drafting context tells the coach where the ceiling sits and the conversation
    tells it too, and a ceiling the coach is told about that differs from the one
    it is judged against is worse than saying nothing. Both callers and the gate
    itself read this, so the stated bound and the enforced bound cannot drift.

    None when there is no norm — the gate abstains there, so there is no bound to
    state and inventing one would put a population figure in front of the coach.
    """
    if not norm_weekly_running_m:
        return None
    return (
        norm_weekly_running_m * MAX_WEEKLY_MULTIPLE,
        norm_weekly_running_m * MAX_SKETCH_MULTIPLE,
    )


@dataclass
class PlanCheck:
    ok: bool = True
    failures: List[str] = field(default_factory=list)
    # The machine-readable companion to `failures`, carrying a code only for the
    # failures a caller acts on differently. Everything else stays prose: a code
    # nothing reads is a vocabulary to maintain for nothing.
    codes: List[str] = field(default_factory=list)

    def fail(self, message: str, *, code: Optional[str] = None) -> None:
        self.ok = False
        self.failures.append(message)
        if code is not None:
            self.codes.append(code)


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
    race: Optional[tuple] = None,
) -> PlanCheck:
    """Everything that must hold before a drafted plan reaches the store.

    `race` is `(date, distance_m)` for the goal race, when one falls inside the
    plan. It exists for the volume ceiling alone: see `_validate_volume`.
    """
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
        _validate_volume(check, week, norm_weekly_running_m, race=race, starts_on=starts_on)

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
        ceilings = volume_ceilings(norm_weekly_running_m)
        if (
            ceilings
            and sketch.target_running_distance_m
            and sketch.target_running_distance_m > ceilings[1]
        ):
            check.fail(
                f"sketched week {sketch.week_start} plans "
                f"{sketch.target_running_distance_m / 1000:.0f} km of running against "
                f"a typical {norm_weekly_running_m / 1000:.0f} km",
                code=VOLUME_CEILING,
            )

    return check


def validate_amendment(
    weeks,
    *,
    rules,
    surviving_by_week,
    today: date,
    starts_on: int = MONDAY,
    norm_weekly_running_m: Optional[float] = None,
    expected_weeks: Optional[Sequence[date]] = None,
    race: Optional[tuple] = None,
) -> PlanCheck:
    """The same coherence gate, applied to a plan being amended in part (#981).

    An amendment rewrites the sessions inside a window and leaves the rest of the
    plan exactly as it was, so what has to hold is that each TOUCHED WEEK still
    works as a week. That is not the same question as "do the new sessions work
    among themselves": a week amended from Wednesday still contains Monday's
    completed run, and a rest-day-after rule spans the join. Judging only the new
    half would let an amendment write precisely the collision the rule set
    exists to forbid, and it would look green doing it.

    So `surviving_by_week` carries what each week keeps, and the rules are
    checked against the union. The plan's own rules are used unchanged: an
    amendment is a change to the sessions, never to the constraints they are
    held to, because rules are the plan's identity and rewriting them silently
    is a redraft wearing an amendment's clothes.
    """
    check = PlanCheck()
    ceilings_norm = norm_weekly_running_m

    if not weeks:
        check.fail("the amendment contains no weeks at all")
        return check

    current_week = week_start(today, starts_on)
    # Every week the window covers has to be answered for. `_apply` clears the
    # whole window and writes back what the amendment holds, so a week the
    # amendment simply omits is not left alone: it is emptied. That turns a
    # coach that ran short into a runner with a blank week they would train,
    # which is the half-applied amendment this module exists to refuse. The
    # prompt does ask for every week, but an instruction to a model is not a
    # check, and this is the one failure mode where being unchecked costs the
    # runner a week rather than a retry.
    if expected_weeks is not None:
        answered = {week.week_start for week in weeks}
        for expected in sorted(set(expected_weeks) - answered):
            check.fail(
                f"week {expected} is inside the window but the amendment says "
                f"nothing about it, which would leave it empty"
            )

    seen = set()
    for week in weeks:
        if week.week_start != week_start(week.week_start, starts_on):
            check.fail(
                f"week {week.week_start} does not start on the runner's week boundary"
            )
        if week.week_start < current_week:
            check.fail(f"week {week.week_start} is in the past")
        if week.week_start in seen:
            check.fail(f"week {week.week_start} appears twice")
        seen.add(week.week_start)

        _validate_sessions(check, week, today, starts_on)

        surviving = list(surviving_by_week.get(week.week_start, ()))
        placeable = [
            _Placeable(
                id=f"kept-{index}",
                intent=row.intent,
                window_start=row.window_start,
                window_end=row.window_end,
            )
            for index, row in enumerate(surviving)
            if row.commitment == "committed"
        ] + [
            _Placeable(
                id=f"{week.week_start}-{index}",
                intent=session.intent,
                window_start=session.window_start,
                window_end=session.window_end,
            )
            for index, session in enumerate(week.sessions)
            if session.commitment == "committed"
        ]
        if placeable:
            satisfiable, violations = check_rules(
                placeable, list(rules) + [_ImplicitRule()], None
            )
            if not satisfiable:
                for violation in violations:
                    check.fail(
                        f"week {week.week_start} cannot satisfy the plan's rule "
                        f"{violation['label']!r} ({violation['statement']}): "
                        f"{violation['detail']}"
                    )

        # The ceiling counts the whole week, kept sessions included. An amendment
        # that added a 20 km run beside two surviving ones would otherwise be
        # measured on its own contribution and pass a week that is absurd.
        ceilings = volume_ceilings(ceilings_norm)
        if ceilings is not None:
            planned = sum(
                planned_distance_m(session)
                for session in week.sessions
                if session.discipline == "run" and session.commitment == "committed"
            ) + sum(
                planned_distance_m(row)
                for row in surviving
                if row.discipline == "run" and row.commitment == "committed"
            )
            # The race is excluded here for the same reason it is excluded from
            # the draft's ceiling: it is the runner's own fixed commitment, not
            # a training volume this gate has a view on. Two ceilings that
            # disagreed about the same week would be a switch with two owners.
            if race is not None:
                race_date, race_distance_m = race
                if week_start(race_date, starts_on) == week.week_start:
                    planned = max(0.0, planned - float(race_distance_m or 0.0))
            if planned > ceilings[0]:
                check.fail(
                    f"week {week.week_start} would hold {planned / 1000:.0f} km of "
                    f"running against a typical "
                    f"{ceilings_norm / 1000:.0f} km",
                    code=VOLUME_CEILING,
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
    check: PlanCheck,
    week,
    norm_weekly_running_m: Optional[float],
    *,
    race: Optional[tuple] = None,
    starts_on: int = MONDAY,
) -> None:
    """An absurdity ceiling against the runner's OWN norm, or no check at all.

    Abstains when there is no norm: a runner with no history is exactly the
    person a population figure would serve worst.

    THE RACE DOES NOT COUNT TOWARDS IT. A goal race is the runner's own decision
    and a fixed distance on a fixed day; it is not a coaching choice this gate
    gets a view on. For a half-marathon runner whose typical week is 21 km the
    race alone IS a typical week, so counting it left race week over the ceiling
    before the coach had prescribed a single training session, and a whole
    twelve-week block was rejected for containing the race it was built for.
    Subtracting it keeps the guard doing its real job: catching a coach that has
    lost the plot about TRAINING volume, in race week as in any other.
    """
    ceilings = volume_ceilings(norm_weekly_running_m)
    if ceilings is None:
        return
    # Rep structure counts toward the ceiling (#876). Reading `target_distance_m`
    # alone scored an interval week's hardest running at zero, which failed
    # PERMISSIVE: the check that exists to catch an absurd week was blind to the
    # sessions most able to make one.
    planned = sum(
        planned_distance_m(session)
        for session in week.sessions
        if session.discipline == "run" and session.commitment == "committed"
    )
    if race is not None:
        race_date, race_distance_m = race
        if week_start(race_date, starts_on) == week.week_start:
            planned = max(0.0, planned - float(race_distance_m or 0.0))
    if planned > ceilings[0]:
        check.fail(
            f"week {week.week_start} plans {planned / 1000:.0f} km of running "
            f"against a typical {norm_weekly_running_m / 1000:.0f} km",
            code=VOLUME_CEILING,
        )
