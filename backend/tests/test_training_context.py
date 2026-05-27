"""Unit tests for the training-context algorithm.

Lifted from test_coach_context.py when the function moved out of the coach
module and into the analysis pipeline as part of M1.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.services.analysis._training_context import build_training_context


def _make_activity(db, user_id, name, start_date):
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=start_date,
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        elev_gain_m=50.0,
        avg_hr=150.0,
        max_hr=175.0,
    )
    db.add(a)
    db.flush()
    return a


def _attach_class(db, activity, activity_class):
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            activity_class=activity_class,
            effort_score=10.0,
            flags=[],
            confidence="high",
            confidence_reasons=[],
        )
    )
    db.flush()


def test_counts_hard_moderate_easy(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"tc_{user_id}@example.com"))
    db.flush()

    base = datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc)

    easy = _make_activity(db, user_id, "Easy", base - timedelta(days=1))
    _attach_class(db, easy, "Easy Run")

    intervals = _make_activity(db, user_id, "Intervals", base - timedelta(days=2))
    _attach_class(db, intervals, "Intervals")

    long_run = _make_activity(db, user_id, "Long", base - timedelta(days=3))
    _attach_class(db, long_run, "Long Run")

    target = _make_activity(db, user_id, "Target", base)

    ctx = build_training_context(db, target)

    assert ctx["intensity_distribution_7d"] == {"easy": 1, "moderate": 1, "hard": 1}
    assert ctx["hard_sessions_this_week"] == 1
    assert ctx["days_since_last_hard"] == 2


def test_no_history_returns_zero_counts(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"tc_{user_id}@example.com"))
    db.flush()

    target = _make_activity(
        db, user_id, "Lonely", datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    )

    ctx = build_training_context(db, target)

    assert ctx["intensity_distribution_7d"] == {"easy": 0, "moderate": 0, "hard": 0}
    assert ctx["hard_sessions_this_week"] == 0
    assert ctx["days_since_last_hard"] is None
