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


def _add_metrics(db, activity, effort="easy", effort_score=3.0, **overrides):
    """Helper to attach a DerivedMetric to an activity."""
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort=effort,
        duration_class=overrides.get("duration_class", "standard"),
        structure=overrides.get("structure", "continuous"),
        is_hilly=overrides.get("is_hilly", False),
        is_race=overrides.get("is_race", False),
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
        hr_zones=overrides.get("hr_zones"),
        hr_zones_source=overrides.get("hr_zones_source"),
    )
    db.add(p)
    db.flush()
    return p


def test_zones_calibrated_true_when_profile_has_strava_zones(db):
    """zones_calibrated should be True when the runner has Strava-sourced HR zones,
    even with no explicitly-sourced max_hr (#458). Strava zones are the binning
    basis, so the coach must not be told they are uncalibrated."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    # Strava zones present; max_hr present but with no source (the real-data case).
    _create_profile(
        db, user_id, max_hr=180, max_hr_source=None,
        hr_zones=[105, 140, 158, 173, 187], hr_zones_source="strava",
    )

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is True
    assert pack.metrics.zones_basis == "strava_zones"


def test_zones_basis_prefers_strava_over_max_hr(db):
    """When both Strava zones and an explicit max_hr exist, Strava zones win the
    basis (they are the actual binning source)."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(
        db, user_id, max_hr=185, max_hr_source="user_entered",
        hr_zones=[105, 140, 158, 173, 187], hr_zones_source="strava",
    )

    pack = build_context_pack(db, activity)

    assert pack.metrics.zones_calibrated is True
    assert pack.metrics.zones_basis == "strava_zones"


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


# --- #441: efficiency curve is coarsened in the coach pack -------------------

def test_efficiency_trend_reads_first_third_vs_last_third():
    from app.services.coach.context import _efficiency_trend

    # Steady curve -> stable.
    assert _efficiency_trend([1.0] * 30) == "stable"
    # Clear fade across the run -> declining.
    assert _efficiency_trend([2.0] * 10 + [1.9] * 10 + [1.5] * 10) == "declining"
    # Clear improvement -> improving.
    assert _efficiency_trend([1.5] * 10 + [1.7] * 10 + [2.0] * 10) == "improving"
    # Stops (0.0) inside a third are ignored, not read as a fade.
    assert _efficiency_trend([2.0, 0.0, 2.0] * 10) == "stable"
    # Degrades to None when there is no usable shape.
    assert _efficiency_trend(None) is None
    assert _efficiency_trend([]) is None
    assert _efficiency_trend([1.0, 2.0]) is None  # fewer than 3 points


def test_summarize_efficiency_drops_curve_keeps_summary():
    from app.services.coach.context import _summarize_efficiency_for_coach

    stored = {
        "average": 2.1,
        "best_sustained": 2.4,
        "unit": "m/min/bpm",
        "curve": [2.0] * 10 + [1.6] * 10,  # full chart-resolution series
    }
    out = _summarize_efficiency_for_coach(stored)
    assert "curve" not in out  # the long series is dropped
    assert out["average"] == 2.1 and out["best_sustained"] == 2.4
    assert out["unit"] == "m/min/bpm"
    assert out["trend"] == "declining"  # coarse shape descriptor survives
    # The stored dict is not mutated.
    assert "curve" in stored

    # Pass-through for empty / None.
    assert _summarize_efficiency_for_coach(None) is None
    assert _summarize_efficiency_for_coach({}) == {}


def test_coach_pack_efficiency_carries_summary_not_full_curve(db):
    """#441: the coach pack carries the efficiency summary + a coarse trend, never
    the full chart-resolution curve."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(
        db,
        activity,
        efficiency_analysis={
            "average": 2.0,
            "best_sustained": 2.3,
            "unit": "m/min/bpm",
            "curve": [2.1] * 15 + [1.5] * 15,
        },
    )

    pack = build_context_pack(db, activity)

    eff = pack.metrics.efficiency_analysis
    assert eff is not None
    assert "curve" not in eff  # the chart series never reaches the coach
    assert eff["average"] == 2.0 and eff["best_sustained"] == 2.3
    assert eff["trend"] == "declining"
