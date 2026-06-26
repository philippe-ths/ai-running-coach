"""Integration tests for M6 perceived-effort in the context pack, plus the
prompt-version discipline (v3 adds the RPE-weighting rule; v1/v2 stay byte-stable).
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V2


def _user(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()
    return user_id


def _activity(db, user_id, start_date, **overrides):
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=start_date,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=50.0,
        average_speed_mps=2.78, raw_summary=overrides.get("raw_summary", {}),
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, activity, **overrides):
    dm = DerivedMetric(
        id=uuid.uuid4(), activity_id=activity.id,
        effort=overrides.get("effort", "easy"),
        duration_class="standard", structure="continuous",
        is_hilly=False, is_race=False,
        effort_score=overrides.get("effort_score", 3.0),
        hr_drift=overrides.get("hr_drift"),
        confidence="high", confidence_reasons=[], flags=[],
        discount_signals=overrides.get("discount_signals"),
    )
    db.add(dm)
    db.flush()
    return dm


def _checkin(db, activity, *, rpe=None, pain_score=None, pain_location=None):
    db.add(CheckIn(id=uuid.uuid4(), activity_id=activity.id, rpe=rpe,
                   pain_score=pain_score, pain_location=pain_location))
    db.flush()


_HEAT = {"hr_drift_pct": 9.0, "likely_inflated_by": ["heat"], "temperature_c": 29.0,
         "confidence": "high", "interpretation": "heat"}


class TestPerceivedEffortInPack:
    def test_heat_suppressed_hard_run_weights_rpe(self, db):
        user_id = _user(db)
        act = _activity(db, user_id, datetime(2026, 3, 1, 10, tzinfo=timezone.utc))
        _metrics(db, act, effort="easy", effort_score=4.0, hr_drift=9.0, discount_signals=_HEAT)
        _checkin(db, act, rpe=8, pain_score=1)
        db.refresh(act)

        pack = build_context_pack(db, act)
        pe = pack.perceived_effort
        assert pe.rpe == 8
        assert pe.effort_axis == "easy"
        assert pe.divergence == 2
        assert pe.divergence_direction == "felt_harder"
        assert pe.hr_confounded is True
        assert pe.recommended_weighting == "rpe_over_hr"

    def test_serialization_folds_out_duplicate_scalars(self, db):
        """One-fact-one-place: the builder populates perceived_effort.rpe/effort_score
        and the drift copies, but serialization drops them — the coach reads each fact
        from its sole home (check_in.rpe, metrics.effort_score, metrics.hr_drift)."""
        user_id = _user(db)
        act = _activity(db, user_id, datetime(2026, 3, 1, 10, tzinfo=timezone.utc))
        _metrics(db, act, effort="easy", effort_score=4.0, hr_drift=9.0, discount_signals=_HEAT)
        _checkin(db, act, rpe=8, pain_score=1)
        db.refresh(act)

        pack = build_context_pack(db, act)
        # The model object still carries them (the derived read is computed from rpe).
        assert pack.perceived_effort.rpe == 8
        assert pack.perceived_effort.effort_score == 4.0

        out = pack.to_serializable_dict()
        # Copies dropped from what the LLM receives.
        assert "rpe" not in out["perceived_effort"]
        assert "effort_score" not in out["perceived_effort"]
        assert "hr_drift_pct" not in out["metrics"]["discount_signals"]
        # Sole homes intact.
        assert out["check_in"]["rpe"] == 8
        assert out["metrics"]["effort_score"] == 4.0
        assert out["metrics"]["hr_drift"] == 9.0
        # The derived read the section exists for survives.
        assert out["perceived_effort"]["divergence"] == 2
        assert out["perceived_effort"]["recommended_weighting"] == "rpe_over_hr"

    def test_no_checkin_degrades_safely(self, db):
        user_id = _user(db)
        act = _activity(db, user_id, datetime(2026, 3, 1, 10, tzinfo=timezone.utc))
        _metrics(db, act, effort="moderate")
        db.refresh(act)

        pack = build_context_pack(db, act)
        pe = pack.perceived_effort
        assert pe.rpe is None
        assert pe.divergence is None
        assert pe.recommended_weighting == "hr_only"
        assert pe.pain_trend is None

    def test_pain_trend_from_history_same_location(self, db):
        user_id = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        # Four prior runs with rising knee pain, then the current run (also knee).
        for i, pain in enumerate([1, 2, 3, 4]):
            a = _activity(db, user_id, base + timedelta(days=i))
            _metrics(db, a)
            _checkin(db, a, rpe=5, pain_score=pain, pain_location="knee")
        current = _activity(db, user_id, base + timedelta(days=4))
        _metrics(db, current)
        _checkin(db, current, rpe=5, pain_score=6, pain_location="knee")
        db.refresh(current)

        pack = build_context_pack(db, current)
        trend = pack.perceived_effort.pain_trend
        assert trend is not None
        assert trend["abstained"] is False
        assert trend["sample_count"] == 5
        assert trend["direction"] == "declining"  # knee pain worsening, lower-is-better

    def test_pain_trend_does_not_conflate_locations(self, db):
        # Knee pain easing in history; current run reports a NEW shin pain. The
        # trend must scope to shin (only this one sample -> abstain), not mix in knee.
        user_id = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        for i, pain in enumerate([8, 6, 4, 2]):
            a = _activity(db, user_id, base + timedelta(days=i))
            _metrics(db, a)
            _checkin(db, a, rpe=5, pain_score=pain, pain_location="knee")
        current = _activity(db, user_id, base + timedelta(days=4))
        _metrics(db, current)
        _checkin(db, current, rpe=5, pain_score=5, pain_location="shin")
        db.refresh(current)

        pack = build_context_pack(db, current)
        trend = pack.perceived_effort.pain_trend
        assert trend == {"abstained": True, "sample_count": 1}  # only the shin sample

    def test_no_pain_location_yields_no_trend(self, db):
        user_id = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        for i, pain in enumerate([1, 2, 3, 4]):
            a = _activity(db, user_id, base + timedelta(days=i))
            _metrics(db, a)
            _checkin(db, a, rpe=5, pain_score=pain)  # no location
        current = _activity(db, user_id, base + timedelta(days=4))
        _metrics(db, current)
        _checkin(db, current, rpe=5, pain_score=6)  # no location
        db.refresh(current)

        pack = build_context_pack(db, current)
        assert pack.perceived_effort.pain_trend is None  # no location anchor


class TestPromptVersioning:
    def test_v4_is_active_default(self):
        # The in-code default tracks the production feature-bearing prompt (#424);
        # v3 remains registered and byte-stable for its cached reports.
        assert settings.COACH_PROMPT_ID == "coach_message_v8"

    def test_v3_adds_perceived_effort_rule(self):
        v3 = PROMPT_VERSIONS["coach_report_v3"]
        assert "PERCEIVED EFFORT" in v3
        assert "rpe_over_hr" in v3
        assert "17." in v3

    def test_v2_stays_byte_stable(self):
        # v2 must not gain the M6 rule; cached v2 reports stay reproducible.
        assert PROMPT_VERSIONS["coach_report_v2"] == SYSTEM_PROMPT_V2
        assert "PERCEIVED EFFORT" not in PROMPT_VERSIONS["coach_report_v2"]

    def test_v1_v2_v3_distinct(self):
        v1 = PROMPT_VERSIONS["coach_report_v1"]
        v2 = PROMPT_VERSIONS["coach_report_v2"]
        v3 = PROMPT_VERSIONS["coach_report_v3"]
        assert v1 != v2 != v3
        assert v2.startswith(v1)  # v2 extends v1
        assert v3.startswith(v2)  # v3 extends v2
