"""build_b_baseline recent-training-summary windowing (#365).

The 7d / 28d / previous_28d summaries are now derived by partitioning a single
56-day fetch in Python instead of three separate queries. These tests pin the
partition boundaries (inclusive start, exclusive end at the activity's own date,
the prev-28d band, and the 56-day lower bound) so the collapse cannot drift from
the prior per-window behavior. Expected values are hand-derived from the window
definitions in build_b_baseline.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import build_b_baseline


def _seed_activity(db, user_id, start_dt, *, distance_m, effort_score):
    activity = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        name="Run",
        type="Run",
        start_date=start_dt,
        distance_m=distance_m,
        moving_time_s=distance_m // 3,
        elapsed_time_s=distance_m // 3,
        elev_gain_m=0.0,
        average_speed_mps=3.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=activity.id,
            effort_score=effort_score,
            confidence="high",
            flags=[],
            confidence_reasons=[],
        )
    )
    db.flush()
    return activity


def test_recent_training_summary_partitions_the_56_day_window(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"bw_{user_id}@example.com"))
    db.flush()

    anchor_dt = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    D = anchor_dt.date()

    # In the 7d window [D-7, D):
    _seed_activity(db, user_id, anchor_dt - timedelta(days=3), distance_m=5000, effort_score=10.0)
    _seed_activity(db, user_id, anchor_dt - timedelta(days=5), distance_m=3000, effort_score=5.0)
    # In 28d but not 7d:
    _seed_activity(db, user_id, anchor_dt - timedelta(days=15), distance_m=8000, effort_score=20.0)
    # In previous_28d [D-56, D-28):
    _seed_activity(db, user_id, anchor_dt - timedelta(days=40), distance_m=6000, effort_score=12.0)
    # Boundary exclude: an activity older than the 56-day span.
    _seed_activity(db, user_id, anchor_dt - timedelta(days=60), distance_m=9000, effort_score=42.0)

    # The anchor itself sits on D; the windows end exclusive at D, so it must
    # not appear in any summary (tests the exclusive upper bound).
    anchor = _seed_activity(db, user_id, anchor_dt, distance_m=10000, effort_score=99.0)
    db.refresh(anchor)

    summary = build_b_baseline(db, anchor).recent_training_summary

    # last_7d: the D-3 and D-5 runs only.
    assert summary.last_7d.activity_count == 2
    assert summary.last_7d.total_distance_m == 8000
    assert summary.last_7d.total_effort == 15.0

    # last_28d: adds the D-15 run.
    assert summary.last_28d.activity_count == 3
    assert summary.last_28d.total_distance_m == 16000
    assert summary.last_28d.total_effort == 35.0

    # previous_28d: only the D-40 run (D-60 is outside the 56-day span).
    assert summary.previous_28d.activity_count == 1
    assert summary.previous_28d.total_distance_m == 6000
    assert summary.previous_28d.total_effort == 12.0
