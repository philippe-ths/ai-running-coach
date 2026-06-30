"""#578: the `intensity` pack section + v14 byte-stability.

The intensity-distribution-and-trend signal reaches the coach as the `intensity` pack
section under v14 only. Pinned here: under v14 the pack MINUS intensity is byte-identical
to v13 (the section moved no other fact); the section appears only under v14 and is
dropped (byte-stably) under any non-intensity prompt and when there is nothing to say;
and the confounder from a stored discount signal exculpates a hard session's hardness.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import build_context_pack

V13 = "coach_message_v13"
V14 = "coach_message_v14"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, *, days_ago=0, effort="easy", time_in_zones=None, discount=None):
    start = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=start, start_date_local=start,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort=effort, duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
        time_in_zones=time_in_zones, discount_signals=discount,
    ))
    db.flush()
    return a


def _seed_window(db, uid, *, n_easy, n_hard, confound_hard=False):
    """A recent window of comparable sessions a few days apart (older than the subject)."""
    discount = {"likely_inflated_by": ["heat"], "interpretation": "x"} if confound_hard else None
    for i in range(n_easy):
        _activity(db, uid, days_ago=2 + i, effort="easy")
    for i in range(n_hard):
        _activity(db, uid, days_ago=2 + n_easy + i, effort="hard", discount=discount)


# --------------------------------------------------------------------------- #
# Byte-stability + surfacing                                                   #
# --------------------------------------------------------------------------- #
def test_v14_pack_minus_intensity_is_byte_identical_to_v13(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=0)

    p14 = build_context_pack(db, subject, prompt_id=V14)
    p13 = build_context_pack(db, subject, prompt_id=V13)

    assert p14.intensity is not None
    d14 = p14.to_serializable_dict()
    d14.pop("intensity", None)
    assert d14 == p13.to_serializable_dict(), "the intensity section moved a fact"


def test_intensity_section_surfaces_distribution_and_band(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=2)

    intensity = build_context_pack(db, subject, prompt_id=V14).intensity
    assert intensity is not None
    assert intensity.this_session.band == "hard"
    assert intensity.has_distribution is True
    assert intensity.session_count == 8
    assert intensity.distribution.easy_pct == 75.0
    assert intensity.distribution.hard_pct == 25.0


# --------------------------------------------------------------------------- #
# Byte-stable drop: non-intensity prompt, nothing-to-say                       #
# --------------------------------------------------------------------------- #
def test_non_intensity_prompt_never_carries_intensity(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=0)

    p13 = build_context_pack(db, subject, prompt_id=V13)
    assert p13.intensity is None
    assert "intensity" not in p13.to_serializable_dict()


def test_no_hr_and_no_history_drops_the_section(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort=None)  # no HR band, alone

    p14 = build_context_pack(db, subject, prompt_id=V14)
    assert p14.intensity is None
    assert "intensity" not in p14.to_serializable_dict()


# --------------------------------------------------------------------------- #
# Confounder exculpation through the real DB read                              #
# --------------------------------------------------------------------------- #
def test_stored_discount_signal_exculpates_hard_sessions(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="easy")
    _seed_window(db, uid, n_easy=4, n_hard=4, confound_hard=True)

    intensity = build_context_pack(db, subject, prompt_id=V14).intensity
    assert intensity.distribution.hard_pct == 50.0          # raw keeps the hard sessions
    assert intensity.distribution_adjusted.hard_pct == 0.0  # exculpated to easy
    assert intensity.confounded_session_count == 4
