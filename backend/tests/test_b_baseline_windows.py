"""#451: the recent-volume consolidation retired the coarse recent_training_summary.

It used to be built here in build_b_baseline by partitioning a 56-day fetch (#365).
This test pins that build_b_baseline no longer carries it, so the lean B baseline
cannot silently regrow the dropped section. The rich `recent_training` successor and
the v11 pack wiring are covered in test_recent_training.py; backward-compat (a
pre-#451 stored pack with the field still validating under extra="forbid") is covered
by test_coach_context_pack.py.
"""

import uuid
from datetime import datetime, timezone

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


def test_b_baseline_drops_retired_recent_training_summary(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"bw_{user_id}@example.com"))
    db.flush()

    anchor = _seed_activity(
        db, user_id, datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        distance_m=10000, effort_score=50.0,
    )
    db.refresh(anchor)

    b = build_b_baseline(db, anchor)
    # The retired section is gone from the lean B baseline (it is no longer built).
    assert not hasattr(b, "recent_training_summary")
