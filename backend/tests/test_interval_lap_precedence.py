"""End-to-end: recorded laps drive the interval structure through analyze() (#170).

Proves the orchestrator prefers the runner's recorded laps over the stream
re-segmentation: the structure carries source="recorded_laps", the rep count
matches the recorded laps (not a stream-merged count), the recorded warmup is
present, and detection_confidence is high.

The lap field shape is the real Strava payload shape (trust level 2); the
velocity stream and the interval arrangement are synthetic test setup.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, User, UserProfile
from app.models.activity_stream import ActivityStream
from app.services.analysis import analyze


def _lap(idx, distance, speed, avg_hr, peak_hr):
    elapsed = int(round(distance / speed))
    return {
        "lap_index": idx,
        "split": idx,
        "name": f"Lap {idx}",
        "distance": float(distance),
        "elapsed_time": elapsed,
        "moving_time": elapsed,
        "average_speed": round(speed, 2),
        "average_heartrate": avg_hr,
        "max_heartrate": peak_hr,
        "pace_zone": 2,
        "resource_state": 2,
    }


def _interval_laps(reps=6):
    laps = [_lap(1, 1200, 3.0, 130.0, 145.0)]  # warmup
    idx = 2
    for i in range(reps):
        laps.append(_lap(idx, 400, 5.0, 178.0, 186.0))
        idx += 1
        if i < reps - 1:
            laps.append(_lap(idx, 200, 2.0, 150.0, 160.0))
            idx += 1
    laps.append(_lap(idx, 800, 2.5, 125.0, 135.0))  # cooldown
    return laps


def _streams(reps=6):
    vel = [3.0] * 400
    hr = [140.0] * 400
    for i in range(reps):
        vel += [5.0] * 80
        hr += [180.0] * 80
        if i < reps - 1:
            vel += [2.0] * 100
            hr += [150.0] * 100
    vel += [2.5] * 320
    hr += [125.0] * 320
    dist, acc = [], 0.0
    for v in vel:
        acc += v
        dist.append(round(acc, 1))
    return vel, hr, dist


def test_recorded_laps_drive_interval_structure(db):
    user_id = uuid.UUID("00000000-0000-0000-0000-0000000001aa")
    act_id = uuid.UUID("00000000-0000-0000-0000-0000000001ab")
    db.add(User(id=user_id, email="laps@example.com"))
    db.add(
        UserProfile(
            user_id=user_id,
            goal_type="5k",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=40,
            max_hr=190,
            max_hr_source="user_entered",
        )
    )
    db.flush()

    vel, hr, dist = _streams(reps=6)
    n = len(vel)
    act = Activity(
        id=act_id,
        user_id=user_id,
        strava_activity_id=987654321,
        name="Track 6x400",
        type="Run",
        start_date=datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc),
        distance_m=5000,
        moving_time_s=n,
        elapsed_time_s=n,
        elev_gain_m=10.0,
        avg_hr=160.0,
        max_hr=185.0,
        avg_cadence=170.0,
        average_speed_mps=3.3,
        user_intent="Intervals",
        raw_summary={"laps": _interval_laps(reps=6)},
    )
    db.add(act)
    db.flush()
    db.add(ActivityStream(activity_id=act.id, stream_type="velocity_smooth", data=vel))
    db.add(ActivityStream(activity_id=act.id, stream_type="heartrate", data=hr))
    db.add(ActivityStream(activity_id=act.id, stream_type="time", data=list(range(n))))
    db.add(ActivityStream(activity_id=act.id, stream_type="distance", data=dist))
    db.commit()

    dm = analyze(db, act.id)

    assert dm is not None
    assert dm.structure == "intervals", f"expected intervals, got {dm.structure}"
    assert dm.interval_structure is not None
    # Laps took precedence over the stream re-segmentation.
    assert dm.interval_structure["source"] == "recorded_laps"
    # 6 recorded work reps, not a stream-merged/oversized count.
    assert dm.interval_structure["summary"]["rep_count"] == 6
    # Recorded warmup is present, not reported as absent.
    assert dm.interval_structure["warmup_duration_s"] is not None
    # Ground-truth structure -> high detection confidence (was capped at medium/low).
    assert dm.workout_match["detection_confidence"] == "high"


def test_stream_fallback_when_no_usable_laps(db):
    """AC#4: with no usable recorded laps, analyze() falls back to the stream
    heuristic. The structure is still produced, but from the stream source
    (no source="recorded_laps" marker)."""
    user_id = uuid.UUID("00000000-0000-0000-0000-0000000002aa")
    act_id = uuid.UUID("00000000-0000-0000-0000-0000000002ab")
    db.add(User(id=user_id, email="streamfallback@example.com"))
    db.add(
        UserProfile(
            user_id=user_id,
            goal_type="5k",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=40,
            max_hr=190,
            max_hr_source="user_entered",
        )
    )
    db.flush()

    vel, hr, dist = _streams(reps=6)
    n = len(vel)
    act = Activity(
        id=act_id,
        user_id=user_id,
        strava_activity_id=987654322,
        name="Track 6x400 (no laps)",
        type="Run",
        start_date=datetime(2026, 5, 2, 7, 0, tzinfo=timezone.utc),
        distance_m=5000,
        moving_time_s=n,
        elapsed_time_s=n,
        elev_gain_m=10.0,
        avg_hr=160.0,
        max_hr=185.0,
        avg_cadence=170.0,
        average_speed_mps=3.3,
        user_intent="Intervals",
        raw_summary={},  # no recorded laps -> lap path abstains
    )
    db.add(act)
    db.flush()
    db.add(ActivityStream(activity_id=act.id, stream_type="velocity_smooth", data=vel))
    db.add(ActivityStream(activity_id=act.id, stream_type="heartrate", data=hr))
    db.add(ActivityStream(activity_id=act.id, stream_type="time", data=list(range(n))))
    db.add(ActivityStream(activity_id=act.id, stream_type="distance", data=dist))
    db.commit()

    dm = analyze(db, act.id)

    assert dm is not None
    assert dm.structure == "intervals", f"expected intervals, got {dm.structure}"
    assert dm.interval_structure is not None
    # Came from the stream heuristic, not recorded laps.
    assert dm.interval_structure.get("source") is None
