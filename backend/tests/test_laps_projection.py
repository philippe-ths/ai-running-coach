"""Recorded Strava laps project into the activity detail response (#208)."""

import uuid
from datetime import datetime, timezone

from app.models import Activity, User
from app.services.laps import project_laps


def _lap(distance, elapsed, speed=None, hr=None, cadence=None):
    lap = {"distance": distance, "elapsed_time": elapsed, "moving_time": elapsed}
    if speed is not None:
        lap["average_speed"] = speed
    if hr is not None:
        lap["average_heartrate"] = hr
    if cadence is not None:
        lap["average_cadence"] = cadence
    return lap


def test_projects_recorded_laps_with_derived_speed():
    raw = {"laps": [_lap(400.0, 90, speed=4.44, hr=175.0, cadence=88.0), _lap(200.0, 120)]}
    laps = project_laps(raw, "Run")
    assert len(laps) == 2
    assert laps[0].lap == 1
    assert laps[0].distance_m == 400.0
    assert laps[0].avg_speed_mps == 4.44
    assert laps[0].avg_hr == 175.0
    # Strava one-leg cadence is normalised to spm like everywhere else.
    assert laps[0].avg_cadence == 176.0
    # Missing average_speed falls back to distance/elapsed.
    assert laps[1].avg_speed_mps == 200.0 / 120


def test_single_whole_run_lap_projects_to_empty():
    raw = {"laps": [_lap(10000.0, 3000, speed=3.33)]}
    assert project_laps(raw, "Run") == []


def test_missing_or_malformed_laps_project_to_empty():
    assert project_laps(None, "Run") == []
    assert project_laps({}, "Run") == []
    assert project_laps({"laps": "garbage"}, "Run") == []
    assert project_laps({"laps": [{"distance": 1.0}, "garbage"]}, "Run") == []


def test_detail_endpoint_includes_laps(client, db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"laps_{user_id}@example.com"))
    db.flush()
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Interval Run",
        type="Run",
        start_date=datetime(2026, 6, 2, 14, 57, tzinfo=timezone.utc),
        distance_m=3660,
        moving_time_s=1200,
        elapsed_time_s=1300,
        elev_gain_m=20.0,
        raw_summary={"laps": [_lap(400.0, 90, speed=4.44), _lap(200.0, 120, speed=1.66)]},
    )
    db.add(a)
    db.commit()

    resp = client.get(f"/api/activities/{a.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["laps"]) == 2
    assert body["laps"][0]["avg_speed_mps"] == 4.44
