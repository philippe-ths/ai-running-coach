"""P3 training_load pack section (ADR 0016) — gated emission + fact invariance.

The section is emitted ONLY under a training-load-aware prompt id (v6), carries the
read-time readiness model as of the subject activity, and adds NOTHING to the facts:
the pack minus training_load is byte-identical to the pre-P3 (v5) pack. The full
cross-condition floor+fact invariance sweep is test_training_load_invariance.py.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import build_context_pack

V6 = "coach_message_v6"
V5 = "coach_message_v5"
_BASE = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _seed(db, n_days, daily_load=60.0):
    """A user with `n_days` of one-run-per-day history; returns the last activity."""
    user = User(email=f"tl-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.commit()
    last = None
    for i in range(n_days):
        when = _BASE + timedelta(days=i)
        act = Activity(
            user_id=user.id, strava_activity_id=int(when.timestamp()),
            start_date=when, type="Run", name="Run", distance_m=8000,
            moving_time_s=2400, elapsed_time_s=2400, elev_gain_m=10.0,
            avg_hr=150, average_speed_mps=3.0, raw_summary={},
        )
        db.add(act)
        db.commit()
        db.refresh(act)
        db.add(DerivedMetric(
            activity_id=act.id, effort="easy", structure="continuous",
            effort_score=daily_load, confidence="high", flags=[], confidence_reasons=[],
        ))
        db.commit()
        last = act
    return last


def test_training_load_emitted_only_under_v6(db):
    activity = _seed(db, 60)
    pack6 = build_context_pack(db, activity, prompt_id=V6)
    pack5 = build_context_pack(db, activity, prompt_id=V5)
    packn = build_context_pack(db, activity)  # no prompt id (default path)

    assert pack6.training_load is not None
    assert pack5.training_load is None
    assert packn.training_load is None
    # Serialization: the key is present under v6, absent (not even null) otherwise.
    assert "training_load" in pack6.to_serializable_dict()
    assert "training_load" not in pack5.to_serializable_dict()
    assert "training_load" not in packn.to_serializable_dict()


def test_training_load_section_reflects_the_model(db):
    activity = _seed(db, 60)  # 60 days history -> chronic baseline established
    tl = build_context_pack(db, activity, prompt_id=V6).training_load
    assert tl.warming_up is False
    assert tl.fitness > 0 and tl.fatigue > 0
    assert tl.condition in {"fresh", "balanced", "fatigued", "overreaching"}
    assert tl.trend in {"building", "steady", "detraining"}
    assert tl.sample_count == 60


def test_training_load_warming_up_for_thin_history(db):
    activity = _seed(db, 3)  # 3 days -> baseline not established
    tl = build_context_pack(db, activity, prompt_id=V6).training_load
    assert tl.warming_up is True
    assert tl.condition == "building_baseline"


def test_training_load_does_not_move_facts(db):
    """AC3 (section level): the pack under v6 minus training_load is byte-identical to
    the v5 pack — adding the section disturbs no other fact, flag, or section."""
    activity = _seed(db, 60)
    d6 = build_context_pack(db, activity, prompt_id=V6).to_serializable_dict()
    d6.pop("training_load", None)
    d5 = build_context_pack(db, activity, prompt_id=V5).to_serializable_dict()
    assert d6 == d5
