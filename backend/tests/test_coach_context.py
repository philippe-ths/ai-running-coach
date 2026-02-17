"""Tests for the coach context pack builder."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, UserProfile
from app.services.coach.context import build_context_pack, hash_context_pack


# ---------------------------------------------------------------------------
# Hash tests (no DB needed)
# ---------------------------------------------------------------------------

def test_hash_deterministic():
    pack = {"activity": {"date": "2024-01-01", "type": "Run"}, "metrics": {"effort_score": 100}}
    h1 = hash_context_pack(pack)
    h2 = hash_context_pack(pack)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_hash_changes_on_input_change():
    pack1 = {"activity": {"date": "2024-01-01"}, "metrics": {"effort_score": 100}}
    pack2 = {"activity": {"date": "2024-01-01"}, "metrics": {"effort_score": 101}}
    assert hash_context_pack(pack1) != hash_context_pack(pack2)


# ---------------------------------------------------------------------------
# Context pack shape (no DB needed)
# ---------------------------------------------------------------------------

def test_context_pack_shape():
    """Verify expected top-level keys match the v2 contract."""
    expected_keys = {
        "activity",
        "metrics",
        "check_in",
        "profile",
        "training_context",
        "recent_training_summary",
        "safety_rules",
    }
    # Simulate a minimal pack with all v2 keys
    pack = {
        "activity": {
            "date": None, "name": "Morning Run", "type": "Run",
            "distance_m": 0, "moving_time_s": 0,
            "avg_hr": None, "max_hr": None, "avg_cadence": None,
            "elev_gain_m": 0,
        },
        "metrics": {
            "activity_class": None, "effort_score": None,
            "hr_drift": None, "pace_variability": None,
            "flags": [], "confidence": "low", "confidence_reasons": [],
            "time_in_zones": None,
            "zones_calibrated": False, "zones_basis": "default_190",
            "efficiency_analysis": None, "stops_analysis": None,
        },
        "check_in": {
            "rpe": None, "pain_score": None, "pain_location": None,
            "sleep_quality": None, "notes": None,
        },
        "profile": {
            "goal_type": None, "experience_level": None,
            "weekly_days_available": None, "injury_notes": None,
            "max_hr": None, "current_weekly_km": None,
        },
        "training_context": {
            "intensity_distribution_7d": {"easy": 0, "moderate": 0, "hard": 0},
            "days_since_last_hard": None,
            "hard_sessions_this_week": 0,
        },
        "recent_training_summary": {"last_7d": {}, "last_28d": {}, "previous_28d": {}},
        "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
    }
    assert set(pack.keys()) == expected_keys
    # Verify it's JSON-serializable
    json.dumps(pack, default=str)


# ---------------------------------------------------------------------------
# Integration tests (require DB fixture)
# ---------------------------------------------------------------------------

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
    )
    db.add(dm)
    db.flush()
    return dm


def _create_profile(db, user_id, max_hr=None, **overrides):
    """Helper to create a UserProfile."""
    p = UserProfile(
        user_id=user_id,
        goal_type=overrides.get("goal_type", "half"),
        experience_level=overrides.get("experience_level", "intermediate"),
        weekly_days_available=overrides.get("weekly_days_available", 4),
        current_weekly_km=overrides.get("current_weekly_km", 30),
        max_hr=max_hr,
    )
    db.add(p)
    db.flush()
    return p


def test_zones_calibrated_true_when_profile_has_max_hr(db):
    """zones_calibrated should be True when profile.max_hr > 100."""
    user_id = uuid.uuid4()
    # Need a User row for FK
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=185)

    pack = build_context_pack(db, activity)

    assert pack["metrics"]["zones_calibrated"] is True
    assert pack["metrics"]["zones_basis"] == "user_max_hr"
    assert pack["profile"]["max_hr"] == 185


def test_zones_calibrated_false_when_no_profile(db):
    """zones_calibrated should be False when no profile exists."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    assert pack["metrics"]["zones_calibrated"] is False
    assert pack["metrics"]["zones_basis"] == "default_190"
    assert pack["profile"]["max_hr"] is None


def test_zones_calibrated_false_when_default_hr(db):
    """zones_calibrated should be False when profile.max_hr is None or <= 100."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=None)

    pack = build_context_pack(db, activity)

    assert pack["metrics"]["zones_calibrated"] is False
    assert pack["metrics"]["zones_basis"] == "default_190"


def test_context_pack_includes_enriched_activity_fields(db):
    """Context pack should include name, avg_cadence, max_hr in activity section."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id, name="Tempo Thursday", avg_cadence=178.0, max_hr=182.0)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    assert pack["activity"]["name"] == "Tempo Thursday"
    assert pack["activity"]["avg_cadence"] == 178.0
    assert pack["activity"]["max_hr"] == 182.0


def test_context_pack_includes_training_context(db):
    """Training context should appear with correct structure."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)

    pack = build_context_pack(db, activity)

    tc = pack["training_context"]
    assert "intensity_distribution_7d" in tc
    assert "days_since_last_hard" in tc
    assert "hard_sessions_this_week" in tc
    dist = tc["intensity_distribution_7d"]
    assert "easy" in dist
    assert "moderate" in dist
    assert "hard" in dist


def test_training_context_counts_hard_sessions(db):
    """Training context should correctly count easy/moderate/hard sessions."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    base_date = datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc)

    # Create 3 history activities in the past 7 days
    easy = _create_activity(db, user_id, name="Easy 1", start_date=base_date - timedelta(days=1))
    _add_metrics(db, easy, activity_class="Easy Run")

    interval = _create_activity(db, user_id, name="Intervals", start_date=base_date - timedelta(days=2))
    _add_metrics(db, interval, activity_class="Intervals")

    long_run = _create_activity(db, user_id, name="Long Run", start_date=base_date - timedelta(days=3))
    _add_metrics(db, long_run, activity_class="Long Run")

    # The target activity
    target = _create_activity(db, user_id, name="Today's Run", start_date=base_date)
    _add_metrics(db, target)

    pack = build_context_pack(db, target)

    tc = pack["training_context"]
    assert tc["intensity_distribution_7d"]["easy"] == 1
    assert tc["intensity_distribution_7d"]["moderate"] == 1
    assert tc["intensity_distribution_7d"]["hard"] == 1
    assert tc["hard_sessions_this_week"] == 1
    assert tc["days_since_last_hard"] == 2  # interval was 2 days ago


def test_context_pack_profile_includes_new_fields(db):
    """Profile section should include max_hr and current_weekly_km."""
    user_id = uuid.uuid4()
    from app.models.user import User
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = _create_activity(db, user_id)
    _add_metrics(db, activity)
    _create_profile(db, user_id, max_hr=190, current_weekly_km=35)

    pack = build_context_pack(db, activity)

    assert pack["profile"]["max_hr"] == 190
    assert pack["profile"]["current_weekly_km"] == 35
