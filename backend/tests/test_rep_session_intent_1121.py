"""#1121: a run the runner marked Tempo or Intervals can complete a rep session.

Since #971 a rep session matches only a run the interval gate found reps in. The
owner's 18 Sep "Tune-up tempo" (1.5 km warm-up, 3x1 km, 1.5 km cool-down) was
run as 1.5 km easy, 3 km continuous at 4:42/km, 1.5 km easy, with the intent set
to Tempo. The gate found no reps, so re-analysis withdrew the tick. The runner's
stated intent is evidence of the work as strong as the detector's; #971's easy
runs carry no such intent and stay unticked.

Session and activity shapes are the production rows; ids are synthetic.
"""

from datetime import date

import pytest

from app.services.schedule import completion
from tests.test_rep_session_matching_971 import _analysed
from tests.test_schedule_completion import (
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)

SEP_18 = date(2026, 9, 18)
WEEK_START = date(2026, 9, 14)
WEEK_END = date(2026, 9, 20)

TUNE_UP = {
    "reps_planned": 3,
    "rep_distance_m": 1000,
    "warmup_distance_m": 1500,
    "cooldown_distance_m": 1500,
}


def _tune_up_tempo(db, plan):
    return _seed_session(
        db, plan, start=WEEK_START, end=WEEK_END, intent="quality",
        title="Tune-up tempo", structure=TUNE_UP,
    )


def _evening_run(db, user, intent):
    run = _seed_activity(
        db, user, day=SEP_18, activity_type="Run",
        distance_m=6097, moving_time_s=1980,
    )
    run.user_intent = intent
    db.commit()
    return _analysed(db, run, None)


@pytest.mark.parametrize("intent", ["Tempo", "Intervals"])
def test_a_run_marked_as_rep_work_completes_the_rep_session(db, intent):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _tune_up_tempo(db, plan)

    assert completion.complete_from_activity(db, _evening_run(db, user, intent)) == session


def test_re_analysis_keeps_the_tick_of_a_run_marked_tempo(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _tune_up_tempo(db, plan)
    run = _evening_run(db, user, "Tempo")
    completion.complete_from_activity(db, run)

    assert completion.correct_auto_credit(db, run) is None
    db.refresh(session)
    assert session.completed_activity_id == run.id


@pytest.mark.parametrize("intent", [None, "Easy Run", "Long Run"])
def test_a_run_not_marked_as_rep_work_still_needs_reps(db, intent):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _tune_up_tempo(db, plan)

    assert completion.find_matching_session(db, _evening_run(db, user, intent)) is None


def test_a_swim_marked_intervals_does_not_complete_a_rep_swim(db):
    """The human's answer was about a run: a swim's "Intervals" is not settled."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    swim_set = _seed_session(
        db, plan, start=WEEK_START, end=WEEK_END, intent="quality",
        discipline="other", title="10x100m", structure={"reps_planned": 10},
    )
    swim_set.activity_type = "Swim"
    db.commit()
    swim = _seed_activity(db, user, day=SEP_18, activity_type="Swim", distance_m=2000)
    swim.user_intent = "Intervals"
    db.commit()

    assert completion.find_matching_session(db, _analysed(db, swim, None)) is None
