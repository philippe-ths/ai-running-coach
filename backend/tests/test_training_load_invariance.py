"""P3 cross-condition invariance — the load-bearing AC3/AC4 gate (ADR 0016).

The deterministic gate (CI, no API key): the `training_load` section is load-bearing
(its condition genuinely changes with the runner's history), yet it never moves the
facts or the safety floor. Across condition states the subject's flags, the calibration
referral, and the deterministic policy outcome on a fixed report are identical; and
under v6 the pack minus training_load is byte-identical to v5. The behavioural
cross-condition LLM run is an owner-flagged pre-flip check, not a unit test.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.schemas.coach import CoachMessageReport
from app.services.coach.context import build_context_pack
from app.services.coach.validator import validate_message_policy

V6 = "coach_message_v6"
V5 = "coach_message_v5"
_BASE = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _seed_flagged_history(db, loads):
    """A user with one run per day carrying `loads` (oldest first); the LAST run is the
    subject and carries a red-flag safety signal. Returns the subject activity."""
    user = User(email=f"tl-inv-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    db.commit()
    subject = None
    n = len(loads)
    for i, load in enumerate(loads):
        when = _BASE + timedelta(days=i)
        is_subject = i == n - 1
        a = Activity(
            user_id=user.id, strava_activity_id=abs(hash((str(user.id), i))) % 10**12,
            start_date=when, type="Run", name="Run", distance_m=12000,
            moving_time_s=4000, elapsed_time_s=4000, elev_gain_m=30.0,
            avg_hr=165, average_speed_mps=3.0, raw_summary={},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        db.add(DerivedMetric(
            activity_id=a.id, effort="moderate", structure="continuous",
            duration_class="long", effort_score=load, confidence="high",
            hr_drift=(9.5 if is_subject else 2.0),
            flags=(["illness_or_extreme_fatigue", "high_drift"] if is_subject else []),
            confidence_reasons=[],
        ))
        db.commit()
        subject = a
    return subject


def _clean():
    return CoachMessageReport(
        message="Solid run. The effort looked controlled and your pace held steady.",
        headline="Solid run", next_steps=[], risks=[], questions=[],
    )


def _overreach():
    return CoachMessageReport(
        message="This is overtraining syndrome — take 600mg of ibuprofen and rest.",
        headline="Overtrained", next_steps=[], risks=[], questions=[],
    )


def test_v6_section_adds_nothing_to_facts_or_floor(db):
    """AC3: under v6 the pack minus training_load is byte-identical to v5 on the same
    flagged history, and the safety floor (referral) is intact and identical in both."""
    subject = _seed_flagged_history(db, [80.0] * 60)
    p6 = build_context_pack(db, subject, prompt_id=V6)
    p5 = build_context_pack(db, subject, prompt_id=V5)

    assert p6.training_load is not None
    d6 = p6.to_serializable_dict()
    d6.pop("training_load", None)
    assert d6 == p5.to_serializable_dict(), "the training_load section moved a fact"
    assert "illness_or_extreme_fatigue" in p6.metrics.flags
    assert p6.calibration.referral is not None
    assert p6.calibration.referral == p5.calibration.referral


def test_condition_is_load_bearing_but_floor_is_condition_invariant(db):
    """AC4 + AC3: different histories produce a different training_load condition
    (load-bearing), while the subject flags, the referral, and the validator outcome
    on fixed reports are identical across conditions (the floor is condition-invariant)."""
    warming = _seed_flagged_history(db, [80.0] * 3)         # thin -> warming up
    established = _seed_flagged_history(db, [80.0] * 60)     # established baseline

    tl_w = build_context_pack(db, warming, prompt_id=V6).training_load
    tl_e = build_context_pack(db, established, prompt_id=V6).training_load
    # Load-bearing: the section genuinely differs across histories.
    assert tl_w.warming_up is True and tl_w.condition == "building_baseline"
    assert tl_e.warming_up is False
    assert tl_w.condition != tl_e.condition or tl_w.fitness != tl_e.fitness

    # Floor + validator condition-invariant.
    outcomes = []
    for subj in (warming, established):
        pack = build_context_pack(db, subj, prompt_id=V6)
        assert pack.calibration.referral is not None
        assert "illness_or_extreme_fatigue" in pack.metrics.flags
        clean = sorted(v.rule for v in validate_message_policy(_clean(), pack))
        bad = sorted(v.rule for v in validate_message_policy(_overreach(), pack))
        outcomes.append((clean, bad))
    assert outcomes[0] == outcomes[1]
    assert "medical_overreach" in outcomes[0][1]  # the gate is real


def test_validator_never_reads_training_load(db):
    """The deterministic policy outcome is identical whether training_load is present
    (v6) or absent (v5) — the validator never reads the section."""
    subject = _seed_flagged_history(db, [80.0] * 60)
    p6 = build_context_pack(db, subject, prompt_id=V6)
    p5 = build_context_pack(db, subject, prompt_id=V5)
    for report in (_clean(), _overreach()):
        r6 = sorted(v.rule for v in validate_message_policy(report, p6))
        r5 = sorted(v.rule for v in validate_message_policy(report, p5))
        assert r6 == r5
