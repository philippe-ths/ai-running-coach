"""Tests for the coach context pack builder.

Hash determinism, shape, and the typed-pack round-trip invariants are covered
in test_coach_context_pack.py. This file covers the build-time behaviour:
zone calibration, training-context pass-through, and profile field exposure.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, UserProfile
from app.services.coach.context import build_context_pack

def _create_activity(db, user_id, name="Morning Run", start_date=None, **overrides):
    """Helper to create a minimal Activity row."""
    if start_date is None:
        start_date = datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc)
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=start_date,
        distance_m=overrides.get("distance_m", 10000),
        moving_time_s=overrides.get("moving_time_s", 3600),
        elapsed_time_s=overrides.get("elapsed_time_s", 3700),
        avg_hr=overrides.get("avg_hr", 150.0),
        max_hr=overrides.get("max_hr", 175.0),
        avg_cadence=overrides.get("avg_cadence", 170.0),
        elev_gain_m=overrides.get("elev_gain_m", 50.0),
        average_speed_mps=overrides.get("average_speed_mps", 2.78),
    )
    db.add(a)
    db.flush()
    return a


def _add_metrics(db, activity, activity_class="Easy Run", effort_score=3.0, **overrides):
    """Helper to attach a DerivedMetric to an activity."""
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        activity_class=activity_class,
        effort_score=effort_score,
        pace_variability=overrides.get("pace_variability"),
        hr_drift=overrides.get("hr_drift"),
        time_in_zones=overrides.get("time_in_zones", {"Z1": 600, "Z2": 1200, "Z3": 600, "Z4": 300, "Z5": 60}),
        flags=overrides.get("flags", []),
        confidence=overrides.get("confidence", "high"),
        confidence_reasons=overrides.get("confidence_reasons", []),
        efficiency_analysis=overrides.get("efficiency_analysis"),
        stops_analysis=overrides.get("stops_analysis"),
        training_context=overrides.get("training_context"),
    )
    db.add(dm)
    db.flush()
    return dm


def _create_profile(db, user_id, max_hr=None, max_hr_source=None, **overrides):
    """Helper to create a UserProfile."""
    p = UserProfile(
        user_id=user_id,
        goal_type=overrides.get("goal_type", "half"),
        experience_level=overrides.get("experience_level", "intermediate"),
        weekly_days_available=overrides.get("weekly_days_available", 4),
        current_weekly_km=overrides.get("current_weekly_km", 30),
        max_hr=max_hr,
        max_hr_source=max_hr_source,
    )
    db.add(p)
    db.flush()
    return p


def test_zones_calibrated_true_when_profile_has_max_hr(db):
    """zones_calibrated should be True when profile.max_hr > 100 AND max_hr_source is set."""
    user_id = uuid.uuid4()
    # Need a User row for FK
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=185, max_hr_source="user_entered")

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is True
    assert pack.metrics.zones_basis == "user_user_entered"
    assert pack.profile.max_hr == 185


def test_zones_calibrated_false_when_no_profile(db):
    """zones_calibrated should be False when no profile exists."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is False
    assert pack.metrics.zones_basis == "uncalibrated"
    assert pack.profile.max_hr is None


def test_zones_calibrated_false_when_default_hr(db):
    """zones_calibrated should be False when profile.max_hr is None or no source."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=None)

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is False
    assert pack.metrics.zones_basis == "uncalibrated"


def test_zones_calibrated_false_when_no_source(db):
    """zones_calibrated should be False when max_hr is set but has no source."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=190, max_hr_source=None)  # No source

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is False
    assert pack.metrics.zones_basis == "uncalibrated"


def test_context_pack_includes_enriched_activity_fields(db):
    """Context pack should include name, avg_cadence, max_hr in activity section."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id, name="Tempo Thursday", avg_cadence=178.0, max_hr=182.0)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    assert pack.activity.name == "Tempo Thursday"
    assert pack.activity.avg_cadence == 178.0
    assert pack.activity.max_hr == 182.0


def test_context_pack_normalizes_raw_strava_cadence(db):
    """Sub-130 raw Strava cadence (strides/min) must be doubled to spm before
    landing in the coach context pack.

    The DB column stores the raw Strava value; the read schema doubles it for
    every user-facing surface. The context pack must apply the same conversion
    so the LLM and the user see the same number for the same activity.
    """
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id, avg_cadence=80.4)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    assert pack.activity.avg_cadence == 160.8


def test_context_pack_passes_training_context_through(db):
    """build_context_pack returns the value persisted on DerivedMetric.training_context."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    persisted = {
        "intensity_distribution_7d": {"easy": 2, "moderate": 1, "hard": 1},
        "days_since_last_hard": 3,
        "hard_sessions_this_week": 1,
    }
    activity = _create_activity(db, user_id)
    _add_metrics(db, activity, training_context=persisted)

    pack = build_context_pack(db, activity)

    assert pack.metrics.training_context == persisted


def test_context_pack_training_context_none_when_unset(db):
    """If DerivedMetric.training_context is unset, the pack carries None."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)  # no training_context override

    pack = build_context_pack(db, activity)

    assert pack.metrics.training_context is None


def test_context_pack_profile_includes_new_fields(db):
    """Profile section should include max_hr, max_hr_source, and current_weekly_km."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=190, max_hr_source="user_entered", current_weekly_km=35)

    pack = build_context_pack(db, activity)

    assert pack.profile.max_hr == 190
    assert pack.profile.max_hr_source == "user_entered"
    assert pack.profile.current_weekly_km == 35
