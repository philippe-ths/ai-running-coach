"""Trends summary carries period aggregates for the graph-card deltas (#385).

`avg_efficiency_mps_per_bpm` and `total_zone_minutes` must be computed over the
current window for ``summary`` and the prior window for ``previous_summary`` so
the Efficiency and Zone-Load graph cards can show a period-over-period delta.
"""

import uuid
from datetime import date, datetime, time, timedelta

from app.models import Activity, DerivedMetric, User
from app.services.trends import get_trends_report


def _user(db) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    return user.id


def _activity(db, user_id, on, *, distance_m, moving_time_s, avg_hr, time_in_zones):
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name="Run",
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=moving_time_s,
        elev_gain_m=0.0,
        avg_hr=avg_hr,
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
            time_in_zones=time_in_zones,
        )
    )
    db.flush()
    return activity


def test_summary_carries_efficiency_and_zone_minutes_per_window(db):
    user_id = _user(db)
    today = date.today()

    # Current 7D window: speed = 3000/1000 = 3.0 m/s, hr = 150
    #   efficiency = 3.0 / 150 = 0.02 mps/bpm
    #   zones = (300+300) easy + 300 mod + (60+40) hard = 1000 s = 16.7 min
    _activity(
        db, user_id, today - timedelta(days=1),
        distance_m=3000, moving_time_s=1000, avg_hr=150,
        time_in_zones={"Z1": 300, "Z2": 300, "Z3": 300, "Z4": 60, "Z5": 40},
    )
    # Previous 7D window: speed = 2000/1000 = 2.0 m/s, hr = 200
    #   efficiency = 2.0 / 200 = 0.01 mps/bpm
    #   zones = (200+100) easy + 100 mod + (50+50) hard = 500 s = 8.3 min
    _activity(
        db, user_id, today - timedelta(days=8),
        distance_m=2000, moving_time_s=1000, avg_hr=200,
        time_in_zones={"Z1": 200, "Z2": 100, "Z3": 100, "Z4": 50, "Z5": 50},
    )

    report = get_trends_report(db, "7D", user_id=user_id)

    assert report.summary.avg_efficiency_mps_per_bpm == 0.02
    assert report.previous_summary.avg_efficiency_mps_per_bpm == 0.01
    assert report.summary.total_zone_minutes == 16.7
    assert report.previous_summary.total_zone_minutes == 8.3


def test_efficiency_is_none_when_no_activity_has_usable_hr(db):
    user_id = _user(db)
    today = date.today()
    # Distance qualifies but no HR → efficiency cannot be computed.
    _activity(
        db, user_id, today - timedelta(days=1),
        distance_m=3000, moving_time_s=1000, avg_hr=None,
        time_in_zones=None,
    )

    report = get_trends_report(db, "7D", user_id=user_id)

    assert report.summary.avg_efficiency_mps_per_bpm is None
    assert report.summary.total_zone_minutes == 0.0
