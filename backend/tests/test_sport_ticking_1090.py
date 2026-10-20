"""#1090: a session that names its exact sport is auto-ticked only by that sport.

Swims, yoga and ball sports all share the `other` discipline, so matching on the
discipline alone let a yoga class fill a planned swim. A session naming no sport
matches as it always did, and the run sports stay one sport.
"""

import pytest

from app.services.schedule import completion
from tests.test_schedule_completion import (
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)


def _named(db, plan, sport, **extra):
    session = _seed_session(db, plan, target_duration_s=3600, **extra)
    session.activity_type = sport
    db.commit()
    return session


def test_a_yoga_class_does_not_tick_a_planned_swim(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _named(db, plan, "Swim", discipline="other", title="Swim")
    yoga = _seed_activity(db, user, activity_type="Yoga", distance_m=0, moving_time_s=3600)

    assert completion.find_matching_session(db, yoga) is None


def test_with_a_swim_and_yoga_both_open_each_activity_fills_its_own(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    swim = _named(db, plan, "Swim", discipline="other", title="Swim")
    yoga_session = _named(db, plan, "Yoga", discipline="other", title="Yoga")
    yoga = _seed_activity(db, user, activity_type="Yoga", distance_m=0, moving_time_s=3600)
    pool = _seed_activity(db, user, activity_type="Swim", distance_m=2000, moving_time_s=3600)

    assert completion.find_matching_session(db, yoga) == yoga_session
    assert completion.find_matching_session(db, pool) == swim


def test_a_session_naming_no_sport_matches_as_before(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    other = _seed_session(db, plan, discipline="other", title="Cross-train", target_duration_s=3600)
    yoga = _seed_activity(db, user, activity_type="Yoga", distance_m=0, moving_time_s=3600)

    assert completion.find_matching_session(db, yoga) == other


@pytest.mark.parametrize("planned, recorded", [("Run", "Run"), ("TrailRun", "Run")])
def test_the_run_sports_count_as_one_sport(db, planned, recorded):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    run_session = _named(db, plan, planned, discipline="run", title="Easy run")
    run = _seed_activity(db, user, activity_type=recorded, moving_time_s=3600)

    assert completion.find_matching_session(db, run) == run_session


def test_a_named_sport_alternative_is_only_filled_by_that_sport(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    session = _seed_session(db, plan, discipline="run", title="Easy run", target_duration_s=3600)
    session.alternatives = [
        {"intent": "easy", "discipline": "other", "activity_type": "Swim",
         "title": "Easy swim", "target_duration_s": 3600},
    ]
    db.commit()
    yoga = _seed_activity(db, user, activity_type="Yoga", distance_m=0, moving_time_s=3600)
    swim = _seed_activity(db, user, activity_type="Swim", distance_m=2000, moving_time_s=3600)

    assert completion.matching_option(session, yoga) is None
    assert completion.matching_option(session, swim) == 1


def test_a_tick_by_hand_ignores_the_named_sport(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    swim = _named(db, plan, "Swim", discipline="other", title="Swim")

    completion.complete_planned_session(db, swim, source=completion.MANUAL)

    assert swim.completed_at is not None
    assert swim.completion_source == completion.MANUAL
