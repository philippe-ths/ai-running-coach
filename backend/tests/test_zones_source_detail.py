"""#301 detail-read projection: GET /api/activities/{id} carries hr_zones_source
from UserProfile so the frontend can caption time-in-zones accurately.

Two cases:
- Strava-sourced zones: UserProfile.hr_zones_source == "strava"
  -> metrics.hr_zones_source == "strava" in the response.
- No stored zones (fallback): UserProfile.hr_zones_source is None
  -> metrics.hr_zones_source is None in the response.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User
from app.models.user_profile import UserProfile

_BASE = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _seed_activity_with_profile(db, *, hr_zones_source):
    """Create a user + profile + activity + derived metrics row.

    `hr_zones_source` is passed through to UserProfile, mirroring what
    sync_athlete_zones writes when it successfully parses Strava zone bounds.
    """
    user = User(email=f"zs-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = UserProfile(
        user_id=user.id,
        goal_type="general",
        experience_level="intermediate",
        weekly_days_available=4,
        hr_zones_source=hr_zones_source,
    )
    db.add(profile)
    db.commit()

    activity = Activity(
        user_id=user.id,
        strava_activity_id=int(_BASE.timestamp()) + uuid.uuid4().int % 100_000,
        start_date=_BASE,
        type="Run",
        name="Test run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=10.0,
        avg_hr=150,
        average_speed_mps=3.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)

    db.add(DerivedMetric(
        activity_id=activity.id,
        effort_score=55.0,
        confidence="high",
        flags=[],
        confidence_reasons=[],
        time_in_zones={"Z1": 120, "Z2": 600, "Z3": 900, "Z4": 480, "Z5": 300},
    ))
    db.commit()
    return activity


def test_detail_metrics_hr_zones_source_strava(client, db):
    """When the runner's profile has hr_zones_source='strava', the detail
    endpoint surfaces that source on metrics.hr_zones_source."""
    activity = _seed_activity_with_profile(db, hr_zones_source="strava")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    assert metrics is not None
    assert metrics["hr_zones_source"] == "strava"


def test_detail_metrics_hr_zones_source_fallback(client, db):
    """When the runner's profile has no stored zones (hr_zones_source is None),
    the detail endpoint returns None for metrics.hr_zones_source, indicating
    the %-of-max-HR fallback was used."""
    activity = _seed_activity_with_profile(db, hr_zones_source=None)

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    assert metrics is not None
    assert metrics["hr_zones_source"] is None
