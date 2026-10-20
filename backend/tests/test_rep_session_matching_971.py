"""#971: an easy run must not complete a rep session.

The observed case, 25 Aug: a 5.16 km easy trail run (stored as a Run), 30 min,
no rep structure.
The window held "Intervals: 5×800m @ 4:40/km", stored structure-only with no
distance or time, and an easy run with a 6 km target. The matcher scored the
structure-only session as an exact fill, so it beat the easy session and the
schedule ticked intervals the runner never ran.

A rep session's prescription is its structure, so it now matches only a run the
interval gate found reps in. A session with nothing to measure still matches,
but ranks behind any session the run can be measured against. A wrong auto-tick
is withdrawn when the run is re-analysed; the runner's own ticks never are.

Session and activity shapes are the issue's production rows; ids are synthetic.
"""

from datetime import date

from app.models import ActivityStream, DerivedMetric
from app.services.schedule import completion
from tests.test_schedule_completion import (
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)

AUG_25 = date(2026, 8, 25)
WEEK_START = date(2026, 8, 24)
WEEK_END = date(2026, 8, 30)

REPS = {"reps_planned": 5, "rep_distance_m": 800, "rest_s": 90}
DETECTED_REPS = {"source": "recorded_laps", "summary": {"rep_count": 5}}


def _intervals(db, plan, **kwargs):
    return _seed_session(
        db, plan, start=WEEK_START, end=WEEK_END, intent="quality",
        title="Intervals: 5×800m @ 4:40/km", structure=REPS, **kwargs,
    )


def _easy(db, plan):
    return _seed_session(
        db, plan, start=WEEK_START, end=WEEK_END, intent="easy",
        title="Easy run", target_distance_m=6000,
    )


def _easy_trail_run(db, user):
    return _seed_activity(
        db, user, day=AUG_25, activity_type="Run",
        distance_m=5160, moving_time_s=1800,
    )


def _analysed(db, activity, interval_structure):
    """What analysis stored: the interval gate's answer for this run."""
    db.add(DerivedMetric(
        activity_id=activity.id, effort_score=30.0, confidence="high",
        interval_structure=interval_structure,
    ))
    db.commit()
    return activity


def test_the_observed_easy_run_completes_the_easy_session_not_the_intervals(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    easy = _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)

    ticked = completion.complete_from_activity(db, run)

    assert ticked == easy
    db.refresh(intervals)
    assert intervals.completed_at is None


def test_an_easy_run_completes_no_rep_session_even_when_it_is_the_only_one(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)

    assert completion.complete_from_activity(db, run) is None
    db.refresh(intervals)
    assert intervals.completed_at is None


def test_a_sized_rep_session_is_not_filled_by_an_easy_run_of_enough_minutes(db):
    """Since #1044 a rep session carries a duration. 30 easy minutes is two
    thirds of 45 and clears the fraction floor; it still is not the session."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _intervals(db, plan, target_duration_s=2700)
    run = _analysed(db, _easy_trail_run(db, user), None)

    assert completion.find_matching_session(db, run) is None


def test_a_run_not_yet_analysed_cannot_claim_a_rep_session(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _intervals(db, plan)

    assert completion.find_matching_session(db, _easy_trail_run(db, user)) is None


def test_a_run_with_reps_completes_the_rep_session(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), DETECTED_REPS)

    assert completion.complete_from_activity(db, run) == intervals


def test_a_session_with_nothing_to_measure_ranks_behind_one_that_can_be(db):
    """Absent is not ideal: an unsized session is no longer an exact fill."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    unsized = _seed_session(
        db, plan, start=WEEK_START, end=WEEK_END, title="Run, as you feel"
    )
    easy = _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)

    assert completion.find_matching_session(db, run) == easy
    easy.completed_at = easy.created_at
    db.commit()
    assert completion.find_matching_session(db, run) == unsized


def test_a_wrong_auto_tick_is_moved_to_the_session_the_run_was(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    easy = _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)
    completion.complete_planned_session(
        db, intervals, source=completion.AUTO, activity=run
    )

    assert completion.find_matching_session(db, run) == easy
    assert completion.correct_auto_credit(db, run) == easy
    db.refresh(intervals)
    assert intervals.completed_at is None
    assert intervals.completed_activity_id is None


def test_the_runners_own_tick_is_never_withdrawn(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)
    completion.complete_planned_session(
        db, intervals, source=completion.MANUAL, activity=run
    )

    assert completion.correct_auto_credit(db, run) is None
    assert completion.find_matching_session(db, run) == intervals
    db.refresh(intervals)
    assert intervals.completion_source == completion.MANUAL


def test_correcting_never_ticks_a_run_that_was_not_credited(db):
    """Re-analysis may withdraw a wrong tick; it must not start ticking history,
    which would re-tick a session the runner deliberately left open."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    easy = _easy(db, plan)
    run = _analysed(db, _easy_trail_run(db, user), None)

    assert completion.correct_auto_credit(db, run) is None
    db.refresh(easy)
    assert easy.completed_at is None


def test_re_analysis_withdraws_a_wrong_tick_end_to_end(db):
    """The real path: the bulk re-analysis derives the gate from stored streams
    (steady pace, no laps: no reps), then corrects the credit."""
    from app.jobs.reanalyze_history import reanalyze_history_batch

    user = _seed_user(db)
    plan = _seed_plan(db, user)
    intervals = _intervals(db, plan)
    easy = _easy(db, plan)
    run = _easy_trail_run(db, user)
    samples = list(range(0, 1800, 10))
    for stream_type, data in {
        "time": samples,
        "distance": [t * 2.87 for t in samples],
        "velocity_smooth": [2.87 for _ in samples],
        "heartrate": [140 for _ in samples],
    }.items():
        db.add(ActivityStream(activity_id=run.id, stream_type=stream_type, data=data))
    db.commit()
    completion.complete_planned_session(
        db, intervals, source=completion.AUTO, activity=run
    )

    reanalyze_history_batch(db, limit=10, user_id=str(user.id))

    db.refresh(intervals)
    db.refresh(easy)
    assert intervals.completed_at is None
    assert easy.completed_activity_id == run.id
