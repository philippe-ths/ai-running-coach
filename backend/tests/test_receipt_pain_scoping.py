"""Regression test for receipt pain scoping (#303).

A receipt pain tap (from the Telegram receipt keyboard, #296 / ADR 0018) records
a CheckIn with pain_score but NO pain_location. This is intentional: the coarse
tap set has no place to capture a body location.

This means receipt pain:
  - IS visible to the location-agnostic M9 referral / red-flag check (three
    notable receipt pains within the window still fire the persistent-pain nudge).
  - Does NOT feed the M6 per-location pain trend, because the trend requires a
    body location to avoid conflating distinct niggles. pain_trend stays absent
    when there is no location anchor.

These tests pin that behaviour as a documented choice, not a silent data drop.
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.user import User
from app.services.coach.context import build_context_pack


# ---------------------------------------------------------------------------
# helpers matching the patterns in test_perceived_effort_context.py and
# test_calibration_context.py
# ---------------------------------------------------------------------------

def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, uid, start_date):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=start_date,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={"average_temp": 15.0},
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, act, *, flags=None):
    dm = DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id,
        effort="easy", duration_class="standard", structure="continuous",
        is_hilly=False, is_race=False, effort_score=3.0,
        hr_drift=4.0, confidence="high", confidence_reasons=[],
        flags=flags or [],
    )
    db.add(dm)
    db.flush()
    return dm


def _receipt_pain(db, act, pain_score):
    """Simulate a receipt pain tap: records pain_score with NO location."""
    db.add(CheckIn(
        id=uuid.uuid4(),
        activity_id=act.id,
        pain_score=pain_score,
        pain_location=None,  # the receipt tap carries no location
    ))
    db.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReceiptPainDoesNotFeedLocationScopedTrend:
    """A receipt pain tap must never produce a location-scoped trend, because
    without a body location the trend cannot be meaningfully scoped."""

    def test_receipt_pain_yields_no_location_scoped_trend(self, db):
        """A single receipt pain (no location) -> pain_trend is None."""
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        act = _activity(db, uid, base)
        _metrics(db, act)
        _receipt_pain(db, act, pain_score=4)
        db.refresh(act)

        pack = build_context_pack(db, act)
        assert pack.perceived_effort.pain_trend is None, (
            "receipt pain with no location must not produce a location-scoped trend"
        )

    def test_receipt_pains_across_runs_yield_no_location_scoped_trend(self, db):
        """Even several receipt pains (all no location) -> pain_trend stays None.

        Without a location anchor the per-location trend degrades to absent,
        regardless of how many location-free pain scores exist. This is the key
        intent: the location-scoped trend must never silently bucket all
        location-free pains under an empty-string location."""
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        for i in range(5):
            a = _activity(db, uid, base + timedelta(days=i))
            _metrics(db, a)
            _receipt_pain(db, a, pain_score=3 + i)
        current = _activity(db, uid, base + timedelta(days=5))
        _metrics(db, current)
        _receipt_pain(db, current, pain_score=7)
        db.refresh(current)

        pack = build_context_pack(db, current)
        assert pack.perceived_effort.pain_trend is None, (
            "multiple receipt pains (no location) must not produce a location-scoped trend"
        )

    def test_receipt_pain_does_not_corrupt_a_located_trend(self, db):
        """Receipt pains (no location) do not contaminate a trend anchored on a real location.

        A runner with a knee niggle plus receipt pains (no location) should see
        only the knee samples in their knee trend -- the location-free pains must
        not be mixed in."""
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        # Four knee-pain runs: 1, 2, 3, 4 (increasing)
        for i, pain in enumerate([1, 2, 3, 4]):
            a = _activity(db, uid, base + timedelta(days=i))
            _metrics(db, a)
            db.add(CheckIn(
                id=uuid.uuid4(), activity_id=a.id,
                pain_score=pain, pain_location="knee",
            ))
            db.flush()
        # Two receipt pains (no location) between the knee runs
        for j in [0, 1]:
            ra = _activity(db, uid, base + timedelta(days=j, hours=12))
            _metrics(db, ra)
            _receipt_pain(db, ra, pain_score=8)
        # Current run: knee, score 5 -- trend should cover only the 5 knee samples
        current = _activity(db, uid, base + timedelta(days=10))
        _metrics(db, current)
        db.add(CheckIn(
            id=uuid.uuid4(), activity_id=current.id,
            pain_score=5, pain_location="knee",
        ))
        db.flush()
        db.refresh(current)

        pack = build_context_pack(db, current)
        trend = pack.perceived_effort.pain_trend
        assert trend is not None
        assert trend["abstained"] is False
        assert trend["sample_count"] == 5, (
            "location-scoped trend must count only knee samples, not receipt pains"
        )


class TestReceiptPainFeedsReferralCheck:
    """Receipt pain (no location) IS visible to the M9 location-agnostic referral
    path. A runner who flags pain via the receipt three times in the recency window
    must still get the persistent-pain referral nudge."""

    def test_three_notable_receipt_pains_fire_referral(self, db):
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        # Three runs with notable receipt pain (score >= 4), all within the
        # _PERSISTENT_PAIN_WINDOW_DAYS window, and no location on any of them.
        for i in range(3):
            a = _activity(db, uid, base + timedelta(days=i))
            _metrics(db, a)
            _receipt_pain(db, a, pain_score=5)
        current = _activity(db, uid, base + timedelta(days=5))
        _metrics(db, current)
        _receipt_pain(db, current, pain_score=6)
        db.refresh(current)

        pack = build_context_pack(db, current)
        assert pack.calibration.referral is not None, (
            "notable receipt pains (no location) must feed the M9 referral check"
        )
        assert pack.calibration.referral["pattern"] == "persistent_pain"

    def test_single_receipt_pain_does_not_fire_referral(self, db):
        """One receipt pain is not enough for the persistent-pain referral."""
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        act = _activity(db, uid, base)
        _metrics(db, act)
        _receipt_pain(db, act, pain_score=7)
        db.refresh(act)

        pack = build_context_pack(db, act)
        assert pack.calibration.referral is None

    def test_receipt_pain_and_located_pain_both_count_for_referral(self, db):
        """The referral path is location-agnostic: both receipt pains and
        in-app located pains count together towards the persistent-pain threshold."""
        uid = _user(db)
        base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
        # One receipt pain (no location) + two in-app located pains = 3 total
        a1 = _activity(db, uid, base)
        _metrics(db, a1)
        _receipt_pain(db, a1, pain_score=5)
        a2 = _activity(db, uid, base + timedelta(days=1))
        _metrics(db, a2)
        db.add(CheckIn(id=uuid.uuid4(), activity_id=a2.id, pain_score=6, pain_location="knee"))
        db.flush()
        current = _activity(db, uid, base + timedelta(days=2))
        _metrics(db, current)
        db.add(CheckIn(id=uuid.uuid4(), activity_id=current.id, pain_score=5, pain_location="shin"))
        db.flush()
        db.refresh(current)

        pack = build_context_pack(db, current)
        assert pack.calibration.referral is not None, (
            "receipt pain and located pain must both count for the location-agnostic referral"
        )
        assert pack.calibration.referral["pattern"] == "persistent_pain"
