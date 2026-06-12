"""Fixed-range windows span exactly N days, not N+1 (#179).

`7D` must cover 7 calendar days (today-6 .. today inclusive), and the
previous-period window must abut it with no gap or overlap so deltas line up.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import get_trends_report, get_weekly_stats


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date, *, distance_m: int = 5000) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        # Midday so the local date is unambiguous regardless of run time.
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=distance_m,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort_score=1.0,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_7d_continuous_charts_span_exactly_7_days(db):
    report = get_trends_report(db, "7D")
    assert len(report.daily_distance) == 7
    assert len(report.daily_time) == 7
    assert len(report.daily_suffer_score) == 7

    today = date.today()
    assert report.daily_distance[0].date == today - timedelta(days=6)
    assert report.daily_distance[-1].date == today


def test_30d_continuous_charts_span_exactly_30_days(db):
    report = get_trends_report(db, "30D")
    assert len(report.daily_distance) == 30
    assert report.daily_distance[0].date == date.today() - timedelta(days=29)
    assert report.daily_distance[-1].date == date.today()


def test_7d_window_boundary_is_inclusive_of_6_days_ago_only(db):
    user_id = _user(db)
    today = date.today()
    # The 7-day window is [today-6, today]. today-7 falls in the PREVIOUS window.
    _activity_on(db, user_id, today - timedelta(days=6), distance_m=1000)  # current edge
    _activity_on(db, user_id, today - timedelta(days=7), distance_m=2000)  # previous edge

    stats = get_weekly_stats(db)

    assert stats.summary.activity_count == 1
    assert stats.summary.total_distance_m == 1000
    assert stats.previous_summary.activity_count == 1
    assert stats.previous_summary.total_distance_m == 2000


def test_previous_window_abuts_current_with_no_gap(db):
    user_id = _user(db)
    today = date.today()
    # Previous window is [today-13, today-7]. today-13 is its earliest day;
    # today-14 must fall outside both windows.
    _activity_on(db, user_id, today - timedelta(days=13), distance_m=3000)
    _activity_on(db, user_id, today - timedelta(days=14), distance_m=9999)

    stats = get_weekly_stats(db)

    assert stats.previous_summary.activity_count == 1
    assert stats.previous_summary.total_distance_m == 3000
