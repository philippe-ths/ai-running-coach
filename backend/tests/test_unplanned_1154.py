"""#1154: the week read marks what was done and matched to no session.

The strip and the day card draw these; the rule is the one the recommendation
already counts extras by, so the screen cannot disagree with it.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import datetime, timezone

from app.models.planned_session import PlannedSession
from app.services.schedule.week import build_week
from tests.test_schedule_week import (
    MON,
    TUE,
    WED,
    _seed_activity,
    _seed_plan,
    _seed_session,
    _seed_user,
)


def _unplanned(week):
    return sorted((a.local_date, a.activity_type) for a in week.logged if a.unplanned)


def test_an_activity_before_the_plan_starts_is_unplanned(db):
    """The owner's Monday: a walk the day before every session's window opens."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, end=WED, discipline="walk", title="Brisk walk")
    _seed_activity(db, user, day=MON, activity_type="Walk", distance_m=4700)

    assert _unplanned(build_week(db, user, today=TUE)) == [(MON, "Walk")]


def test_an_activity_that_ticked_a_session_is_not_unplanned(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    walk = _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=6000)
    session = _seed_session(
        db, plan, start=TUE, end=WED, discipline="walk",
        completed_at=datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc),
    )
    session.completed_activity_id = walk.id
    db.commit()

    assert _unplanned(build_week(db, user, today=TUE)) == []


def test_a_hand_ticked_session_claims_its_own_recording(db):
    """Ticked by hand, then recorded too: one walk, not a done session plus an
    unplanned walk beside it."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=TUE, end=WED, discipline="walk",
        completed_at=datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc),
    )
    _seed_activity(db, user, day=TUE, activity_type="Walk", distance_m=6000)

    assert _unplanned(build_week(db, user, today=TUE)) == []


def test_with_no_plan_nothing_is_marked_unplanned(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=MON, activity_type="Walk", distance_m=4700)

    week = build_week(db, user, today=TUE)
    assert len(week.logged) == 1
    assert _unplanned(week) == []
