"""#830: the deterministic gate a drafted plan passes before it is stored.

Coercion proves the plan is well-SHAPED; this proves it is COHERENT. The check
that earns the whole rule vocabulary is here: a week whose own rules admit no
legal arrangement is REJECTED, and the failure names the rule in the way. Also
pinned: the deliberate NON-checks — no population ramp rule, no cap on quality
sessions, and a volume ceiling that abstains entirely when the runner has no
history of their own to be measured against.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta

import pytest

from app.schemas.schedule import SpacingRule
from app.services.schedule.draft_contract import (
    DraftedPlan,
    DraftedSession,
    DraftedWeek,
    SketchedWeek,
)
from app.services.schedule.plan_validator import (
    MAX_WEEKLY_MULTIPLE,
    validate_drafted_plan,
)

# 2026-08-10 is a Monday; 2026-08-09 the Sunday before it.
MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
FRI = MON + timedelta(days=4)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)
NEXT_MON = MON + timedelta(days=7)
SUNDAY_WEEK_START = MON - timedelta(days=1)


def _session(**overrides) -> DraftedSession:
    payload = {
        "window_start": TUE,
        "window_end": TUE,
        "intent": "easy",
        "discipline": "run",
        "title": "Easy 8k",
        "target_distance_m": 8000,
    }
    payload.update(overrides)
    return DraftedSession(**payload)


def _plan(*, weeks=(), sketch_weeks=(), rules=()) -> DraftedPlan:
    return DraftedPlan(
        rules=list(rules), weeks=list(weeks), sketch_weeks=list(sketch_weeks)
    )


def _week(start=MON, sessions=()) -> DraftedWeek:
    return DraftedWeek(week_start=start, sessions=list(sessions))


def _failures(check) -> str:
    return " | ".join(check.failures)


# --- the plan that passes ---------------------------------------------------


def test_a_well_formed_plan_passes():
    plan = _plan(
        rules=[
            SpacingRule(
                kind="rest_day_after", label="A full rest day after the long run",
                intent="long",
            ),
            SpacingRule(
                kind="no_intent_day_before",
                label="No quality run the day before the long run",
                before_intent="quality",
                target_intent="long",
            ),
        ],
        weeks=[
            _week(
                MON,
                [
                    _session(window_start=TUE, window_end=WED, title="Easy 8k"),
                    _session(
                        window_start=THU,
                        window_end=THU,
                        intent="quality",
                        title="6x800m",
                        target_duration_s=3000,
                        target_distance_m=None,
                        reps_planned=6,
                        rep_distance_m=800,
                    ),
                    _session(
                        window_start=SUN,
                        window_end=SUN,
                        intent="long",
                        title="Long run",
                        target_distance_m=18000,
                    ),
                    _session(
                        window_start=MON,
                        window_end=MON,
                        intent="rest",
                        discipline="other",
                        title="Rest",
                        target_distance_m=None,
                    ),
                ],
            )
        ],
        sketch_weeks=[
            SketchedWeek(
                week_start=NEXT_MON,
                phase="build",
                target_running_distance_m=45000,
                sessions_by_discipline={"run": 4},
                intent_counts={"easy": 2, "long": 1, "quality": 1},
            )
        ],
    )

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=40000)

    assert check.ok is True
    assert check.failures == []


def test_a_plan_with_no_weeks_at_all_is_not_a_plan():
    check = validate_drafted_plan(_plan(), today=MON)

    assert check.ok is False
    assert "no weeks at all" in _failures(check)


# --- weeks ------------------------------------------------------------------


def test_a_week_that_does_not_start_on_the_runners_week_boundary_is_rejected():
    check = validate_drafted_plan(_plan(weeks=[_week(WED)]), today=MON)

    assert check.ok is False
    assert "does not start on the runner's week boundary" in _failures(check)


def test_a_week_in_the_past_is_rejected():
    check = validate_drafted_plan(
        _plan(weeks=[_week(MON - timedelta(days=7))]), today=MON
    )

    assert check.ok is False
    assert f"week {MON - timedelta(days=7)} is in the past" in _failures(check)


def test_the_same_week_given_twice_is_rejected():
    check = validate_drafted_plan(_plan(weeks=[_week(MON), _week(MON)]), today=MON)

    assert check.ok is False
    assert f"week {MON} appears twice" in _failures(check)


def test_a_week_given_as_both_concrete_and_sketched_is_rejected():
    """Two answers for one week, with nothing to say which the runner follows."""
    plan = _plan(
        weeks=[_week(MON, [_session()])],
        sketch_weeks=[SketchedWeek(week_start=MON, sessions_by_discipline={"run": 3})],
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "given as both concrete and sketched" in _failures(check)


def test_a_sketched_week_is_held_to_the_same_boundary_and_clock():
    plan = _plan(
        sketch_weeks=[
            SketchedWeek(week_start=WED),
            SketchedWeek(week_start=MON - timedelta(days=7)),
        ]
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "sketched week" in _failures(check)
    assert "does not start on the runner's week boundary" in _failures(check)
    assert "is in the past" in _failures(check)


# --- sessions ---------------------------------------------------------------


def test_a_session_whose_window_sits_in_a_different_week_than_its_parent_is_rejected():
    """`sessions_in_range` places a session in a week by its `window_start`, so a
    session filed under the wrong week would put its load in one week and its day
    in another."""
    plan = _plan(
        weeks=[_week(MON, [_session(window_start=NEXT_MON, window_end=NEXT_MON)])]
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert f"sits in week {NEXT_MON}, not {MON}" in _failures(check)


def test_a_window_that_crosses_a_week_boundary_is_rejected():
    plan = _plan(weeks=[_week(MON, [_session(window_start=SAT, window_end=NEXT_MON)])])

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "crosses a week boundary" in _failures(check)


def test_a_session_entirely_in_the_past_is_rejected():
    """The current week is legitimate; a session inside it that has already
    lapsed is not — the runner cannot act on it."""
    plan = _plan(weeks=[_week(MON, [_session(window_start=MON, window_end=MON)])])

    check = validate_drafted_plan(plan, today=WED)

    assert check.ok is False
    assert "is entirely in the past" in _failures(check)


def test_a_session_still_open_today_is_not_in_the_past():
    plan = _plan(weeks=[_week(MON, [_session(window_start=MON, window_end=THU)])])

    check = validate_drafted_plan(plan, today=WED)

    assert check.ok is True


def test_a_rest_day_carrying_a_training_target_is_rejected():
    plan = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        intent="rest",
                        discipline="other",
                        title="Rest",
                        target_distance_m=5000,
                    )
                ],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "carries a training target" in _failures(check)


def test_a_session_with_neither_a_distance_nor_a_duration_is_rejected():
    """It cannot be sized, so its load abstains and it draws no bar — the runner
    would be shown a card with nothing on it."""
    plan = _plan(
        weeks=[_week(MON, [_session(target_distance_m=None, target_duration_s=None)])]
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "nothing can size it" in _failures(check)


def test_a_duration_alone_sizes_a_session_perfectly_well():
    plan = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        discipline="strength",
                        intent="strength",
                        title="Gym",
                        target_distance_m=None,
                        target_duration_s=2700,
                    )
                ],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is True


# --- THE check the rule vocabulary exists to make possible -------------------


def test_a_week_that_cannot_satisfy_its_own_rules_is_rejected_and_names_the_rule():
    """A coach that writes "no quality the day before the long run" and then pins
    both to consecutive days has written a week the runner cannot follow. Caught
    here, rather than discovered by the runner on Saturday.

    Both sessions are PINNED, so the constraint search has exactly one candidate
    arrangement and it violates the rule. Dropping the day-before rule makes the
    week arrangeable again, which is what names it as the rule in the way.
    """
    plan = _plan(
        rules=[
            SpacingRule(
                kind="rest_day_after",
                label="A full rest day after the long run",
                intent="long",
            ),
            SpacingRule(
                kind="no_intent_day_before",
                label="No quality run the day before the long run",
                before_intent="quality",
                target_intent="long",
            ),
        ],
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=SUN,
                        window_end=SUN,
                        intent="long",
                        title="Long run",
                        target_distance_m=20000,
                    ),
                    _session(
                        window_start=SAT,
                        window_end=SAT,
                        intent="quality",
                        title="8x400m",
                        target_distance_m=9000,
                        reps_planned=8,
                        rep_distance_m=400,
                    ),
                ],
            )
        ],
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    failures = _failures(check)
    assert "cannot satisfy its own rule" in failures
    assert "No quality run the day before the long run" in failures
    # The rule that is NOT in the way is not blamed for the week.
    assert "A full rest day after the long run" not in failures


def test_the_same_week_with_a_floating_quality_session_is_accepted():
    """The sensitivity half of the check above: only the PINNING made it
    impossible. Give the quality session a window and a legal week exists, so the
    rules are not treated as violations in themselves."""
    plan = _plan(
        rules=[
            SpacingRule(
                kind="no_intent_day_before",
                label="No quality run the day before the long run",
                before_intent="quality",
                target_intent="long",
            ),
        ],
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=SUN,
                        window_end=SUN,
                        intent="long",
                        title="Long run",
                        target_distance_m=20000,
                    ),
                    _session(
                        window_start=TUE,
                        window_end=SAT,
                        intent="quality",
                        title="8x400m",
                        target_distance_m=9000,
                        reps_planned=8,
                        rep_distance_m=400,
                    ),
                ],
            )
        ],
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is True


def test_a_suggestion_is_not_held_to_the_weeks_rules():
    """Only committed sessions are arranged: ignoring a suggestion leaves no
    trace, so it cannot be the reason a week is called unarrangeable."""
    plan = _plan(
        rules=[
            SpacingRule(
                kind="no_intent_day_before",
                label="No quality run the day before the long run",
                before_intent="quality",
                target_intent="long",
            ),
        ],
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=SUN,
                        window_end=SUN,
                        intent="long",
                        title="Long run",
                        target_distance_m=20000,
                    ),
                    _session(
                        window_start=SAT,
                        window_end=SAT,
                        intent="quality",
                        commitment="suggested",
                        title="Optional strides",
                        target_distance_m=6000,
                    ),
                ],
            )
        ],
    )

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is True


def test_a_rule_the_checker_cannot_evaluate_is_reported_rather_than_waved_through():
    """A rule nobody can check cannot be enforced, and a plan that claimed one
    would claim a discipline it never had. The closed vocabulary is enforced at
    coercion, so this is the belt-and-braces path underneath it."""

    class _UncheckableRule:
        kind = "phase_of_the_moon"
        label = "Long runs only on a waxing gibbous"

    plan = _plan(weeks=[_week(MON, [_session()])])
    plan.rules = [_UncheckableRule()]

    check = validate_drafted_plan(plan, today=MON)

    assert check.ok is False
    assert "not one the checker understands" in _failures(check)


# --- the volume ceiling, and its deliberate abstention ----------------------


def test_the_volume_ceiling_fires_above_twice_the_runners_own_norm():
    """An absurdity ceiling, not a coaching opinion: it catches a plan that
    prescribes 100 km to a 20 km runner, not one that ramps deliberately."""
    norm = 20000.0
    plan = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=TUE, window_end=TUE, title="Long",
                        target_distance_m=norm * MAX_WEEKLY_MULTIPLE + 1000,
                    )
                ],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=norm)

    assert check.ok is False
    assert "km of running against a typical" in _failures(check)


def test_a_bold_but_not_absurd_week_is_left_alone():
    """No 10% rule and no cap on quality sessions live here. Whether this week is
    right for this runner is the judgment the coach is FOR."""
    norm = 20000.0
    plan = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=TUE, window_end=TUE, title="Big week",
                        target_distance_m=norm * MAX_WEEKLY_MULTIPLE - 1000,
                    )
                ],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=norm)

    assert check.ok is True


def test_the_ceiling_abstains_entirely_for_a_runner_with_no_norm():
    """A DELIBERATE North Star decision, not an oversight.

    The ceiling is measured against this runner's own history and there is no
    population fallback, because a runner with no history is exactly the person a
    population figure serves worst. With no norm the check does not fire at all —
    a 200 km week from a runner the app knows nothing about is passed through
    here, and it is the coach's judgment (and the runner's own reading of the
    plan) that is trusted, not an invented median.
    """
    absurd = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=TUE, window_end=TUE, title="Two hundred km",
                        target_distance_m=200_000,
                    )
                ],
            )
        ]
    )

    for no_norm in (None, 0.0):
        check = validate_drafted_plan(
            absurd, today=MON, norm_weekly_running_m=no_norm
        )
        assert check.ok is True, f"norm={no_norm!r} should abstain, not fire"


def test_only_committed_running_counts_toward_the_ceiling():
    norm = 20000.0
    plan = _plan(
        weeks=[
            _week(
                MON,
                [
                    _session(
                        window_start=TUE, window_end=TUE, title="Committed",
                        target_distance_m=norm,
                    ),
                    _session(
                        window_start=WED, window_end=WED, title="Suggested",
                        commitment="suggested", target_distance_m=norm,
                    ),
                    _session(
                        window_start=THU, window_end=THU, title="Bike",
                        discipline="bike", target_distance_m=80000,
                    ),
                ],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=norm)

    assert check.ok is True


def test_a_sketched_weeks_running_target_is_held_to_the_same_ceiling():
    plan = _plan(
        sketch_weeks=[
            SketchedWeek(week_start=NEXT_MON, target_running_distance_m=200_000)
        ]
    )

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=20000)

    assert check.ok is False
    assert f"sketched week {NEXT_MON} plans" in _failures(check)


# --- the runner's own week boundary ----------------------------------------


def test_a_sunday_start_runners_weeks_are_judged_on_their_own_boundary():
    """The week boundary is the runner's (`week_starts_on`), so a plan is legal or
    illegal relative to THEIR week, not the server's default Monday."""
    sunday_plan = _plan(
        weeks=[
            _week(
                SUNDAY_WEEK_START,
                [
                    _session(
                        window_start=MON,
                        window_end=WED,
                        title="Easy 8k",
                        target_distance_m=8000,
                    )
                ],
            )
        ]
    )

    for_sunday_runner = validate_drafted_plan(
        sunday_plan, today=MON, starts_on=6
    )
    for_monday_runner = validate_drafted_plan(sunday_plan, today=MON, starts_on=0)

    assert for_sunday_runner.ok is True
    # The same plan is wrong for a Monday runner: the week starts mid-week and the
    # session no longer sits in the week that claims it.
    assert for_monday_runner.ok is False
    assert "does not start on the runner's week boundary" in _failures(
        for_monday_runner
    )


def test_a_window_legal_on_a_monday_week_can_cross_a_sunday_runners_boundary():
    """SAT..SUN is one week for a Monday runner and two for a Sunday one."""
    plan = _plan(weeks=[_week(MON, [_session(window_start=SAT, window_end=SUN)])])

    assert validate_drafted_plan(plan, today=MON, starts_on=0).ok is True

    sunday = validate_drafted_plan(plan, today=MON, starts_on=6)
    assert sunday.ok is False
    assert "crosses a week boundary" in _failures(sunday)


@pytest.mark.parametrize("starts_on", [0, 6])
def test_the_current_week_is_never_treated_as_past_for_either_boundary(starts_on):
    from app.services.weeks import week_start

    current = week_start(THU, starts_on)
    plan = _plan(
        weeks=[
            _week(
                current,
                [_session(window_start=THU, window_end=FRI, target_distance_m=8000)],
            )
        ]
    )

    check = validate_drafted_plan(plan, today=THU, starts_on=starts_on)

    assert check.ok is True


# --- regressions found during review of this slice --------------------------


def test_a_week_beyond_the_configured_horizon_is_rejected():
    """The count caps bound how MANY weeks a plan holds, not how far out they sit.

    Without a reach check a plan could put a week two years away and pass every
    other gate, because nothing tied a drafted week to the horizon the coach was
    asked for.
    """
    plan = _plan(
        sketch_weeks=[SketchedWeek(week_start=MON + timedelta(days=7 * 60))]
    )

    check = validate_drafted_plan(
        plan, today=MON, horizon_weeks=12, norm_weekly_running_m=None
    )

    assert check.ok is False
    assert any("past the 12-week horizon" in f for f in check.failures)


def test_a_plan_within_its_horizon_passes_the_reach_check():
    plan = _plan(
        sketch_weeks=[SketchedWeek(week_start=MON + timedelta(days=7 * 11))]
    )

    check = validate_drafted_plan(
        plan, today=MON, horizon_weeks=12, norm_weekly_running_m=None
    )

    assert check.ok is True


def test_a_week_stacked_absurdly_high_on_one_day_is_rejected():
    """An absurdity floor the coach does not have to remember to write.

    Every other nonsense has a floor; this one relied on the model policing
    itself, so a week could pin all fourteen permitted sessions to one Tuesday
    unless the coach happened to author a max_sessions_per_day rule.
    """
    stacked = [
        _session(
            window_start=TUE,
            window_end=TUE,
            title=f"Session {index}",
            target_duration_s=1800,
        )
        for index in range(6)
    ]
    plan = _plan(weeks=[_week(sessions=stacked)], rules=[])

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=None)

    assert check.ok is False
    assert any("sensible" in f or "sessions fall on" in f for f in check.failures)


def test_a_normal_number_of_sessions_in_a_day_is_left_alone():
    """Three in a day is a real thing a runner does — a walk, a run and the gym.
    The floor catches the impossible, it does not express an opinion."""
    same_day = [
        _session(
            window_start=TUE,
            window_end=TUE,
            title=f"Session {index}",
            target_duration_s=1800,
        )
        for index in range(3)
    ]
    plan = _plan(weeks=[_week(sessions=same_day)], rules=[])

    check = validate_drafted_plan(plan, today=MON, norm_weekly_running_m=None)

    assert check.ok is True


def test_rep_structure_alone_is_enough_to_size_a_session():
    """"8 x 400m off 90 seconds" is a fully specified session.

    Demanding a distance or duration on top rejected three real interval sessions
    in a live draft. The requirement was wrong, not the plan: a coach would never
    also express that session as a total distance, and inventing one from a
    warm-up multiplier nobody stated would be worse than pricing it at the
    runner's per-session median.
    """
    intervals = _session(
        intent="quality",
        title="8x400m",
        target_distance_m=None,
        reps_planned=8,
        rep_distance_m=400,
        rest_s=90,
    )

    check = validate_drafted_plan(
        _plan(weeks=[_week(sessions=[intervals])]),
        today=MON,
        norm_weekly_running_m=None,
    )

    assert check.ok is True, _failures(check)


def test_a_session_with_nothing_to_size_it_is_still_rejected():
    nothing = _session(intent="easy", title="A run, somehow", target_distance_m=None)

    check = validate_drafted_plan(
        _plan(weeks=[_week(sessions=[nothing])]),
        today=MON,
        norm_weekly_running_m=None,
    )

    assert check.ok is False
    assert any("nothing can size it" in f for f in check.failures)
