"""A2a: the consolidated stream view is produced during analysis and stored on
the DerivedMetric row, retrievable by activity. (The pure downsampling contract
is covered in test_stream_view.py; this pins the production + persistence seam.)
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, User
from app.models.activity_stream import ActivityStream
from app.services.analysis import analyze
from app.services.analysis.stream_view import STREAM_VIEW_MAX_POINTS


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"sv_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id):
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run",
        type="Run",
        start_date=datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3000,
        elapsed_time_s=3050,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=40.0,
        average_speed_mps=3.3,
        raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


def test_analyze_produces_and_stores_stream_view(db):
    user_id = _user(db)
    activity = _activity(db, user_id)

    n = 900
    db.add(ActivityStream(activity_id=activity.id, stream_type="time", data=list(range(n))))
    db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=[150] * n))
    db.add(ActivityStream(activity_id=activity.id, stream_type="velocity_smooth", data=[3.3] * n))
    db.add(ActivityStream(activity_id=activity.id, stream_type="cadence", data=[170] * n))
    db.add(ActivityStream(activity_id=activity.id, stream_type="grade_smooth", data=[1.0] * n))
    db.commit()

    dm = analyze(db, activity.id)
    assert dm is not None

    view = dm.stream_view
    assert view is not None
    # lean + bounded
    assert view["n_points"] <= STREAM_VIEW_MAX_POINTS
    assert view["source_n"] == n
    assert len(view["hr"]) == view["n_points"]
    assert view["hr"][0] == 150
    assert view["pace_s_per_km"][0] == round(1000.0 / 3.3)


def test_analyze_without_streams_stores_none_stream_view(db):
    user_id = _user(db)
    activity = _activity(db, user_id)
    db.commit()

    dm = analyze(db, activity.id)
    assert dm is not None
    assert dm.stream_view is None
