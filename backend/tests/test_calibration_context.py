"""M9 calibration in the coach context pack (DB-integration).

Confirms build_context_pack gathers same-bucket comparable drifts and feeds the
pure calibrator (personal expected drift once enough comparable runs exist, else
the labeled heuristic), and that the non-diagnostic referral fires on the
computable red-flag patterns from real flags/check-ins.
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V5


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, uid, day, *, temp=15.0):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 3, day, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={"average_temp": temp},
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, act, *, hr_drift, effort="easy", is_hilly=False, flags=None):
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id, effort=effort, duration_class="standard",
        structure="continuous", is_hilly=is_hilly, is_race=False, effort_score=3.0,
        hr_drift=hr_drift, confidence="high", confidence_reasons=[], flags=flags or [],
    ))
    db.flush()


def _checkin(db, act, *, pain_score):
    db.add(CheckIn(id=uuid.uuid4(), activity_id=act.id, pain_score=pain_score))
    db.flush()


def test_personal_calibration_once_enough_comparable_runs(db):
    uid = _user(db)
    # four prior easy/flat/cool runs with ~5.5% drift
    for i, d in enumerate([1, 2, 3, 4]):
        a = _activity(db, uid, d)
        _metrics(db, a, hr_drift=[5.0, 6.0, 5.0, 6.0][i])
    current = _activity(db, uid, 6)
    _metrics(db, current, hr_drift=12.0)  # well above personal norm

    cal = build_context_pack(db, current).calibration
    assert cal.hr_drift["calibrated"] is True
    assert cal.hr_drift["expected_drift_pct"] == 5.5
    assert cal.hr_drift["comparison"] == "above"
    assert cal.referral is None


def test_thin_history_uses_labeled_heuristic(db):
    uid = _user(db)
    a = _activity(db, uid, 1)
    _metrics(db, a, hr_drift=5.0)
    current = _activity(db, uid, 3)
    _metrics(db, current, hr_drift=12.0)
    cal = build_context_pack(db, current).calibration
    assert cal.hr_drift["calibrated"] is False
    assert "heuristic" in cal.hr_drift["basis"].lower()


def test_calibration_matches_only_same_bucket(db):
    """Prior runs in a different context bucket (hilly) are not comparable."""
    uid = _user(db)
    for d in [1, 2, 3, 4]:
        a = _activity(db, uid, d)
        _metrics(db, a, hr_drift=5.0, is_hilly=True)  # hilly bucket
    current = _activity(db, uid, 6)
    _metrics(db, current, hr_drift=12.0, is_hilly=False)  # flat bucket
    cal = build_context_pack(db, current).calibration
    assert cal.hr_drift["calibrated"] is False  # no flat comparables


def test_referral_fires_on_illness_flag(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current, hr_drift=6.0, flags=["illness_or_extreme_fatigue"])
    cal = build_context_pack(db, current).calibration
    assert cal.referral is not None
    assert cal.referral["pattern"] == "multi_signal_fatigue"
    assert "diagnos" not in cal.referral["nudge"].lower()


def test_referral_fires_on_persistent_pain(db):
    uid = _user(db)
    for d in [1, 2, 3]:
        a = _activity(db, uid, d)
        _metrics(db, a, hr_drift=5.0)
        _checkin(db, a, pain_score=5)
    current = _activity(db, uid, 5)
    _metrics(db, current, hr_drift=5.0)
    _checkin(db, current, pain_score=6)
    cal = build_context_pack(db, current).calibration
    assert cal.referral is not None
    assert cal.referral["pattern"] == "persistent_pain"


def test_no_referral_when_clean(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current, hr_drift=5.0)
    cal = build_context_pack(db, current).calibration
    assert cal.referral is None


class TestPromptVersioning:
    def test_v6_is_active_default(self):
        # Active default has since advanced (#168 -> v8); v7 stays byte-stable.
        assert settings.COACH_PROMPT_ID == "coach_report_v10"

    def test_v6_adds_calibration_rule(self):
        v6 = PROMPT_VERSIONS["coach_report_v7"]
        assert "CALIBRATED CORRECTION" in v6
        assert "20." in v6
        assert "non-diagnostic" in v6.lower()

    def test_v5_stays_byte_stable(self):
        assert PROMPT_VERSIONS["coach_report_v5"] == SYSTEM_PROMPT_V5
        assert "CALIBRATED CORRECTION" not in PROMPT_VERSIONS["coach_report_v5"]

    def test_v6_extends_v5(self):
        v5 = PROMPT_VERSIONS["coach_report_v5"]
        v6 = PROMPT_VERSIONS["coach_report_v7"]
        assert v6 != v5
        assert v6.startswith(v5)


def test_stale_pain_outside_window_does_not_fire_referral(db):
    """Old unrelated niggles spread beyond the recency window are not 'persistent'."""
    uid = _user(db)
    # three notable pain readings, but on days far apart and >42d before current
    for d in [1, 2, 3]:
        a = _activity(db, uid, d)
        _metrics(db, a, hr_drift=5.0)
        _checkin(db, a, pain_score=5)
    # current run ~2 months later, no recent pain
    current = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={"average_temp": 15.0},
    )
    db.add(current); db.flush()
    _metrics(db, current, hr_drift=5.0)
    cal = build_context_pack(db, current).calibration
    assert cal.referral is None  # stale pain is outside the window
