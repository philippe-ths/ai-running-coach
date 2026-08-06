"""Cross-user isolation: _query_activity_facts must not leak across users (#197).

When two users each have activities in the same date range, queries for one user
must return only that user's activities. These tests prove the bug (before the fix,
they fail because there is no user_id filter) and pin the correct behaviour after.
"""

import uuid
from datetime import date, datetime, time, timedelta

import pytest

from app.models import Activity, DerivedMetric, User
from app.services.activity_facts import query_facts as _query_activity_facts
from app.services.trends import (
    build_activity_facts,
    get_trends_report,
    get_weekly_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity_on(db, user_id, on: date, *, distance_m: int = 5000) -> Activity:
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_query_activity_facts_scoped_to_user(db):
    """_query_activity_facts with a user_id must exclude another user's activities."""
    today = date.today()
    user_a = _user(db)
    user_b = _user(db)

    _activity_on(db, user_a, today - timedelta(days=1), distance_m=1000)
    _activity_on(db, user_b, today - timedelta(days=1), distance_m=9999)

    facts_a = _query_activity_facts(db, today - timedelta(days=7), None, user_id=user_a)
    assert len(facts_a) == 1
    assert facts_a[0].distance_m == 1000, "user_b's 9999m activity must not appear for user_a"


def test_query_activity_facts_user_b_sees_only_own(db):
    """Symmetric: user_b's query must not include user_a's activity."""
    today = date.today()
    user_a = _user(db)
    user_b = _user(db)

    _activity_on(db, user_a, today - timedelta(days=1), distance_m=1000)
    _activity_on(db, user_b, today - timedelta(days=1), distance_m=9999)

    facts_b = _query_activity_facts(db, today - timedelta(days=7), None, user_id=user_b)
    assert len(facts_b) == 1
    assert facts_b[0].distance_m == 9999


def test_build_activity_facts_scoped_to_user(db):
    """build_activity_facts with a user_id must exclude another user's activities."""
    today = date.today()
    user_a = _user(db)
    user_b = _user(db)

    _activity_on(db, user_a, today - timedelta(days=1), distance_m=2000)
    _activity_on(db, user_b, today - timedelta(days=1), distance_m=8888)

    facts = build_activity_facts(db, "7D", user_id=user_a)
    assert all(f.distance_m != 8888 for f in facts), "user_b's activity must not appear"
    assert any(f.distance_m == 2000 for f in facts)


def test_get_weekly_stats_scoped_to_user(db):
    """get_weekly_stats with a user_id must exclude another user's activities."""
    user_a = _user(db)
    user_b = _user(db)

    _activity_on(db, user_a, date.today() - timedelta(days=1), distance_m=3000)
    # user_b has a much larger activity in the same window
    _activity_on(db, user_b, date.today() - timedelta(days=1), distance_m=50000)

    stats = get_weekly_stats(db, user_id=user_a)

    assert stats.summary.activity_count == 1
    assert stats.summary.total_distance_m == 3000, (
        "user_b's 50km must not inflate user_a's stats"
    )


def test_get_trends_report_scoped_to_user(db):
    """get_trends_report with a user_id must exclude another user's activities."""
    user_a = _user(db)
    user_b = _user(db)

    _activity_on(db, user_a, date.today() - timedelta(days=1), distance_m=4000)
    _activity_on(db, user_b, date.today() - timedelta(days=1), distance_m=60000)

    report = get_trends_report(db, "7D", user_id=user_a)

    assert report.summary.activity_count == 1
    assert report.summary.total_distance_m == 4000, (
        "user_b's 60km must not appear in user_a's trends report"
    )
