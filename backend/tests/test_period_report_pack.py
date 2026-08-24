"""#946: the period-report pack — what the coach reads to review a runner-chosen
stretch of training, filtered by period AND by discipline.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.period_report_pack import (
    MAX_PACK_SESSIONS,
    build_period_report_pack,
)

TODAY = date(2026, 8, 10)


def _seed_user(db) -> User:
    user = User(email=f"period-report-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_activity(
    db,
    user: User,
    *,
    day: date,
    activity_type: str = "Run",
    distance_m: float = 8000,
    moving_time_s: int = 2400,
    effort_score: float = 30.0,
) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0),
        type=activity_type,
        name=activity_type,
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(
        DerivedMetric(
            activity_id=activity.id, effort_score=effort_score, confidence="high"
        )
    )
    db.commit()
    return activity


def test_pack_only_includes_activities_inside_the_period(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY - timedelta(days=1))  # just before
    inside = _seed_activity(db, user, day=TODAY)
    inside_end = _seed_activity(db, user, day=TODAY + timedelta(days=5))
    _seed_activity(db, user, day=TODAY + timedelta(days=6))  # just after

    pack = build_period_report_pack(
        db,
        user,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=5),
        disciplines=[],
    )

    ids = {s.activity_id for s in pack.sessions}
    assert ids == {str(inside.id), str(inside_end.id)}
    assert pack.totals["sessions"] == 2
    assert not pack.is_empty


def test_period_end_is_inclusive(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)

    pack = build_period_report_pack(
        db, user, period_start=TODAY, period_end=TODAY, disciplines=[]
    )

    assert pack.totals["sessions"] == 1


def test_pack_filters_by_discipline(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY, activity_type="Run")
    _seed_activity(db, user, day=TODAY + timedelta(days=1), activity_type="Ride")

    pack = build_period_report_pack(
        db,
        user,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=3),
        disciplines=["Run"],
    )

    assert pack.totals["sessions"] == 1
    assert pack.sessions[0].type == "Run"
    assert pack.disciplines == ["Run"]


def test_pack_with_no_discipline_filter_includes_every_type(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY, activity_type="Run")
    _seed_activity(db, user, day=TODAY + timedelta(days=1), activity_type="Ride")

    pack = build_period_report_pack(
        db,
        user,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=3),
        disciplines=[],
    )

    assert pack.totals["sessions"] == 2


def test_empty_period_has_no_sessions_and_is_flagged_empty(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY - timedelta(days=30))  # well outside

    pack = build_period_report_pack(
        db,
        user,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=6),
        disciplines=[],
    )

    assert pack.is_empty
    assert pack.sessions == []
    assert pack.totals["sessions"] == 0


def test_sessions_are_bounded_and_chronological(db):
    user = _seed_user(db)
    for offset in range(0, MAX_PACK_SESSIONS + 10):
        _seed_activity(db, user, day=TODAY + timedelta(days=offset))

    pack = build_period_report_pack(
        db,
        user,
        period_start=TODAY,
        period_end=TODAY + timedelta(days=MAX_PACK_SESSIONS + 20),
        disciplines=[],
    )

    assert len(pack.sessions) == MAX_PACK_SESSIONS
    dates = [s.date for s in pack.sessions]
    assert dates == sorted(dates)
    # The newest sessions survive the bound, not the oldest.
    assert dates[-1] == (TODAY + timedelta(days=MAX_PACK_SESSIONS + 9)).isoformat()


def test_pack_carries_no_stream_data_and_no_per_activity_focus_payload(db):
    """The North Star's first question: a period review needs the shape of the
    stretch, not one run's raw stream. The pack model is `extra="forbid"`, so
    this is a shape assertion, not a convention."""
    user = _seed_user(db)
    _seed_activity(db, user, day=TODAY)

    pack = build_period_report_pack(
        db, user, period_start=TODAY, period_end=TODAY, disciplines=[]
    )

    dumped = pack.model_dump()
    assert "streams" not in dumped
    assert "focus" not in dumped
    for session in dumped["sessions"]:
        assert "splits" not in session
        assert "interval" not in session
