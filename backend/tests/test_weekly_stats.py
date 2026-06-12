"""Rolling 7-day dashboard summary, with the prior 7 days for comparison (#246, #248).

These totals must agree with the Trends 7D view, so the windows mirror it:
current = [today-7d, now), previous = [today-14d, today-7d). "Hard days" comes
from the effort axis, not an HR/name heuristic.
"""

import uuid
from datetime import datetime, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import get_weekly_stats


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity(
    db,
    user_id,
    *,
    days_ago: float,
    distance_m: int = 5000,
    moving_time_s: int = 1500,
    effort: str | None = None,
    effort_score: float = 0.0,
    type: str = "Run",
) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.now() - timedelta(days=days_ago),
        type=type,
        name="Run",
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort=effort,
            effort_score=effort_score,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_current_window_sums_distance_time_count_and_load(db):
    user_id = _user(db)
    _activity(db, user_id, days_ago=1, distance_m=5000, moving_time_s=1500, effort_score=10.0)
    _activity(db, user_id, days_ago=3, distance_m=8000, moving_time_s=2400, effort_score=20.0)

    stats = get_weekly_stats(db)

    assert stats.summary.total_distance_m == 13000
    assert stats.summary.total_moving_time_s == 3900
    assert stats.summary.activity_count == 2
    assert stats.summary.total_load == 30.0


def test_previous_window_is_separate_from_current(db):
    user_id = _user(db)
    # current
    _activity(db, user_id, days_ago=2, distance_m=4000, effort_score=5.0)
    # previous 7 days
    _activity(db, user_id, days_ago=9, distance_m=6000, effort_score=15.0)
    _activity(db, user_id, days_ago=11, distance_m=1000, effort_score=2.0)

    stats = get_weekly_stats(db)

    assert stats.summary.activity_count == 1
    assert stats.summary.total_distance_m == 4000
    assert stats.previous_summary.activity_count == 2
    assert stats.previous_summary.total_distance_m == 7000
    assert stats.previous_summary.total_load == 17.0


def test_activities_outside_both_windows_are_excluded(db):
    user_id = _user(db)
    _activity(db, user_id, days_ago=3, distance_m=5000)
    _activity(db, user_id, days_ago=20, distance_m=9999)  # older than previous window

    stats = get_weekly_stats(db)

    assert stats.summary.activity_count == 1
    assert stats.previous_summary.activity_count == 0
    assert stats.previous_summary.total_distance_m == 0


def test_hard_days_counts_distinct_days_with_tempo_or_hard_effort(db):
    user_id = _user(db)
    # Two hard activities on the same day count once.
    _activity(db, user_id, days_ago=2.0, effort="hard")
    _activity(db, user_id, days_ago=2.1, effort="hard")
    # A tempo day on a different day.
    _activity(db, user_id, days_ago=4, effort="tempo")
    # Easy / unclassified days don't count.
    _activity(db, user_id, days_ago=1, effort="easy")
    _activity(db, user_id, days_ago=5, effort=None)

    stats = get_weekly_stats(db)

    assert stats.summary.hard_days == 2


def test_deleted_activities_are_ignored(db):
    user_id = _user(db)
    keep = _activity(db, user_id, days_ago=2, distance_m=5000)
    gone = _activity(db, user_id, days_ago=3, distance_m=5000)
    gone.is_deleted = True
    db.flush()

    stats = get_weekly_stats(db)

    assert stats.summary.activity_count == 1
    assert stats.summary.total_distance_m == 5000
    assert keep.is_deleted is False
