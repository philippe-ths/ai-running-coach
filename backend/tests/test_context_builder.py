"""Integration tests for ContextPack builder."""

import pytest
from datetime import datetime, timedelta
import random
from app.models import User, Activity, DerivedMetric, CheckIn, UserProfile
from app.services.ai.context_builder import build_context_pack
from app.db.session import SessionLocal

@pytest.fixture
def db_session():
    """Yield a database session (rollback after test)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

from uuid import uuid4

def test_build_context_pack_integration(db_session):
    """Test building a full ContextPack from inserted DB data."""
    
    # 1. Setup Data
    user = User(email=f"test_builder_{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()

    profile = UserProfile(
        user_id=user.id,
        goal_type="marathon",
        experience_level="intermediate",
        weekly_days_available=4
    )
    db_session.add(profile)

    activity = Activity(
        user_id=user.id,
        strava_activity_id=random.randint(1000000, 999999999),
        start_date=datetime.utcnow(),
        type="Run",
        name="Test Long Run",
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3600,
        elev_gain_m=100.0,
        avg_hr=150.0 # Signal: Heart Rate
    )
    db_session.add(activity)
    db_session.flush()

    metric = DerivedMetric(
        activity_id=activity.id,
        activity_class="Long Run",
        effort_score=120.0,
        confidence="high",
        flags=["high_drift"]
    )
    db_session.add(metric)

    check_in = CheckIn(
        activity_id=activity.id,
        rpe=7,
        notes="Felt good but tired at end"
    )
    db_session.add(check_in)
    db_session.commit()

    # 2. Execute Builder
    pack = build_context_pack(activity.id, db_session)

    # 3. Assertions
    assert pack is not None
    
    # Activity Identity
    assert pack.activity.id == str(activity.id)
    assert pack.activity.distance_m == 10000.0
    
    # Athlete Context
    assert pack.athlete.goal == "marathon"
    assert pack.athlete.experience_level == "intermediate"

    # Metrics matches
    effort_metric = next((m for m in pack.derived_metrics if m.key == "effort_score"), None)
    assert effort_metric is not None
    assert effort_metric.value == 120.0
    assert effort_metric.unit == "TRIMP"

    # Flags matches
    flag = next((f for f in pack.flags if f.code == "high_drift"), None)
    assert flag is not None
    assert flag.severity == "warn" # inferred from 'high' keyword in our mock logic

    # Check-in matches
    assert pack.check_in.rpe_0_10 == 7
    assert pack.check_in.notes == "Felt good but tired at end"

    # Signals
    assert "heart_rate" in pack.available_signals # inferred from avg_hr=150
    assert "elevation" in pack.available_signals # inferred from elev_gain_m=100
    assert "gps" not in pack.available_signals # no map data provided
    assert "splits" not in pack.available_signals # no distance stream provided (mock)
    
    # Metadata
    assert pack.generated_at_iso is not None


def test_context_pack_cadence_normalization(db_session):
    """Test that cadence is normalized in the ContextPack without changing DB."""
    
    # 1. Setup Activity with low cadence (strides/min like Strava)
    user = User(email=f"cadence_test_{uuid4()}@example.com")
    db_session.add(user)
    db_session.flush()

    activity = Activity(
        user_id=user.id,
        strava_activity_id=random.randint(1000000, 999999999),
        start_date=datetime.utcnow(),
        type="Run",
        name="Low Cadence Run",
        distance_m=5000,
        moving_time_s=1800,
        elapsed_time_s=1800,
        avg_cadence=79.1  # Strides per min
    )
    db_session.add(activity)
    db_session.commit()

    # 2. Execute Builder
    pack_low = build_context_pack(activity.id, db_session)
    assert pack_low.activity.avg_cadence == 158.2  # Should be doubled

    # 3. Setup Activity with normal cadence (SPM)
    activity.avg_cadence = 168.0
    db_session.commit()

    pack_normal = build_context_pack(activity.id, db_session)
    assert pack_normal.activity.avg_cadence == 168.0  # Should stay same

    # 4. Verify DB value remains unchanged
    db_session.refresh(activity)
    assert activity.avg_cadence == 168.0
