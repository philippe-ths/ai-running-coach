"""#297: end-to-end check that `analyze` bins time-in-zone against the runner's
own Strava HR zones (stored on the profile) so the result lines up with Strava.

Oracle: this runner's real Strava zones, 0-124 / 125-154 / 155-169 / 170-184 /
185+. A steady 160 bpm aerobic effort is Z3 on Strava; under the old generic
%-of-max-HR scheme (Z4 floor 152 at max 190) the same effort read as Z4 — the
exact mismatch reported in #297.
"""
import uuid
from datetime import datetime, timezone

from app.models import Activity, User, UserProfile
from app.models.activity_stream import ActivityStream
from app.services.analysis import analyze

_STRAVA_HR_ZONE_BOUNDS = [0, 125, 155, 170, 185]


def _seed_activity(db, *, hr_zones):
    user = User(email=f"zones_{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.flush()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
            max_hr_source="user_entered",
            hr_zones=hr_zones,
            hr_zones_source="strava" if hr_zones else None,
        )
    )
    activity = Activity(
        user_id=user.id,
        strava_activity_id=int(uuid.uuid4().int % (10**12)),
        name="Steady aerobic run",
        type="Run",
        start_date=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=300,
        elapsed_time_s=300,
        elev_gain_m=20.0,
        avg_hr=160.0,
        max_hr=169.0,
        average_speed_mps=3.3,
    )
    db.add(activity)
    db.flush()
    n = 300
    db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=[160] * n))
    db.add(ActivityStream(activity_id=activity.id, stream_type="velocity_smooth", data=[3.3] * n))
    db.add(ActivityStream(activity_id=activity.id, stream_type="time", data=list(range(n))))
    db.commit()
    return activity


def test_analyze_uses_strava_zones_when_present(db):
    activity = _seed_activity(db, hr_zones=_STRAVA_HR_ZONE_BOUNDS)

    dm = analyze(db, activity.id)

    assert dm is not None
    # 160 bpm sits in the runner's Strava Z3 (155-169), matching Strava — not Z4.
    assert dm.time_in_zones["Z3"] == 300
    assert dm.time_in_zones["Z4"] == 0


def test_analyze_falls_back_to_percent_max_without_zones(db):
    """Without stored zones the generic %max scheme still runs (and misfiles the
    same 160 bpm effort as Z4 — the behaviour #297 corrects via zone sync)."""
    activity = _seed_activity(db, hr_zones=None)

    dm = analyze(db, activity.id)

    assert dm is not None
    assert dm.time_in_zones["Z4"] == 300
