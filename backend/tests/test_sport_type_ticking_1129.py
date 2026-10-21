"""#1129: a session naming a sport is ticked by that sport whatever Strava's
legacy `type` says.

`PlannedSession.activity_type` holds Strava's `SportType` names, but
`Activity.type` holds the legacy `type`, which folds many sports into a coarser
one. On the production snapshot a Tennis, Padel and PhysicalTherapy session are
stored as `Workout`, a GravelRide as `Ride`, a Canoeing as `WaterSport`, and a
TrailRun as `Run`. Matching the named sport against the legacy type meant those
recordings never ticked their own session, and the re-analysis withdrew such a
tick once made.
"""

import pytest

from app.services.schedule import completion
from tests.test_rep_session_matching_971 import _analysed
from tests.test_schedule_completion import (
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)


def _named(db, plan, sport, discipline):
    session = _seed_session(
        db, plan, discipline=discipline, title=sport, target_duration_s=3600
    )
    session.activity_type = sport
    db.commit()
    return session


def _recorded(db, user, legacy_type, sport_type):
    """A recording as Strava stores it: the coarse legacy `type`, with the
    exact sport only in the summary."""
    activity = _seed_activity(
        db, user, activity_type=legacy_type, distance_m=0, moving_time_s=3600
    )
    activity.raw_summary = {"type": legacy_type, "sport_type": sport_type}
    db.commit()
    return activity


@pytest.mark.parametrize(
    "legacy_type, sport_type, discipline",
    [
        ("Workout", "Tennis", "other"),
        ("Workout", "Padel", "other"),
        ("Ride", "GravelRide", "bike"),
        ("WaterSport", "Canoeing", "other"),
    ],
)
def test_a_recording_ticks_the_session_naming_its_exact_sport(
    db, legacy_type, sport_type, discipline
):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _named(db, plan, sport_type, discipline)
    activity = _recorded(db, user, legacy_type, sport_type)

    assert completion.complete_from_activity(db, activity) == session


def test_a_legacy_type_alone_does_not_fill_a_different_named_sport(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _named(db, plan, "Tennis", "other")
    padel = _recorded(db, user, "Workout", "Padel")

    assert completion.find_matching_session(db, padel) is None


def test_a_correct_tick_survives_re_analysis(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _named(db, plan, "Tennis", "other")
    tennis = _analysed(db, _recorded(db, user, "Workout", "Tennis"), None)
    completion.complete_from_activity(db, tennis)

    assert completion.correct_auto_credit(db, tennis) is None
    db.refresh(session)
    assert session.completed_activity_id == tennis.id


def test_a_trail_run_stored_as_run_still_ticks_a_planned_trail_run(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _named(db, plan, "TrailRun", "run")
    trail = _recorded(db, user, "Run", "TrailRun")

    assert completion.find_matching_session(db, trail) == session
