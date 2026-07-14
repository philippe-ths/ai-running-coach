"""ADR 0026 Slice 3 (#673): the merged `this_run.intensity_read` + promoted
`this_run.referral` + `right_now.intensity_mix` in the real pack.

Pinned here: under the grouped-v3 prompt the four this-run intensity lenses collapse into
`intensity_read` (and the standalone perceived_effort/calibration/intensity sections
retire), the recent distribution moves to `intensity_mix`, and the safety referral is
promoted to its own key; and grouped-v3 MINUS the three new sections is byte-identical to
grouped-v2 MINUS the three retired sections (the merge moved no other fact). Every prior
prompt keeps the four separate lenses (covered by the wider suite's byte-stability pins).
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import build_context_pack

V14 = "coach_message_v14"
GROUPED2 = "coach_message_lean_grouped_v2"
GROUPED3 = "coach_message_lean_grouped_v3"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, *, days_ago=0, effort="easy", flags=None, discount=None,
              time_in_zones=None):
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
        hr_drift=8.2, confidence="high", confidence_reasons=[], flags=flags or [],
        time_in_zones=time_in_zones, discount_signals=discount,
    ))
    db.flush()
    return a


def _seed_window(db, uid, *, n_easy, n_hard, confound_hard=False):
    discount = {"likely_inflated_by": ["heat"], "interpretation": "x"} if confound_hard else None
    for i in range(n_easy):
        _activity(db, uid, days_ago=2 + i, effort="easy")
    for i in range(n_hard):
        _activity(db, uid, days_ago=2 + n_easy + i, effort="hard", discount=discount)


# --------------------------------------------------------------------------- #
# grouped_v3 surfaces the merged read and retires the four lenses             #
# --------------------------------------------------------------------------- #
def test_grouped_v3_emits_intensity_read_and_retires_the_four_lenses(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=2)

    pack = build_context_pack(db, subject, prompt_id=GROUPED3)
    d = pack.to_serializable_dict()

    assert pack.intensity_read is not None
    assert pack.intensity_read.band == "hard"
    assert "intensity_read" in d
    assert "intensity_mix" in d
    # The three standalone lenses retire under grouped_v3.
    assert pack.perceived_effort is None
    assert pack.calibration is None
    assert pack.intensity is None
    for retired in ("perceived_effort", "calibration", "intensity"):
        assert retired not in d


def test_grouped_v3_intensity_mix_carries_the_recent_distribution(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=2)

    mix = build_context_pack(db, subject, prompt_id=GROUPED3).intensity_mix
    assert mix is not None
    assert mix.sessions == 8
    assert mix.distribution.easy_pct == 75.0
    assert mix.distribution.hard_pct == 25.0


# --------------------------------------------------------------------------- #
# byte-stability: the merge moved no OTHER fact                                #
# --------------------------------------------------------------------------- #
def test_grouped_v3_minus_new_equals_grouped_v2_minus_retired(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=2)

    d3 = build_context_pack(db, subject, prompt_id=GROUPED3).to_serializable_dict()
    d2 = build_context_pack(db, subject, prompt_id=GROUPED2).to_serializable_dict()

    for k in ("intensity_read", "referral", "intensity_mix"):
        d3.pop(k, None)
    for k in ("perceived_effort", "calibration", "intensity"):
        d2.pop(k, None)
    assert d3 == d2, "the intensity merge moved a fact outside the swapped sections"


# --------------------------------------------------------------------------- #
# referral promotion                                                          #
# --------------------------------------------------------------------------- #
def test_referral_promoted_to_its_own_key_under_grouped_v3(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard",
                        flags=["illness_or_extreme_fatigue"])
    _seed_window(db, uid, n_easy=6, n_hard=0)

    pack = build_context_pack(db, subject, prompt_id=GROUPED3)
    assert isinstance(pack.referral, str)
    assert "professional" in pack.referral.lower()
    assert "referral" in pack.to_serializable_dict()


def test_referral_absent_when_no_red_flag(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid, n_easy=6, n_hard=0)

    pack = build_context_pack(db, subject, prompt_id=GROUPED3)
    assert pack.referral is None
    assert "referral" not in pack.to_serializable_dict()


def test_prior_prompt_keeps_referral_inside_calibration(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard",
                        flags=["illness_or_extreme_fatigue"])
    _seed_window(db, uid, n_easy=6, n_hard=0)

    pack = build_context_pack(db, subject, prompt_id=V14)
    assert pack.referral is None                       # not promoted under a prior prompt
    assert pack.calibration is not None
    assert pack.calibration.referral is not None       # still rides calibration


# --------------------------------------------------------------------------- #
# confounder linkage through the real DB read                                 #
# --------------------------------------------------------------------------- #
def test_stored_discount_signal_links_the_drift_read(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard",
                        discount={"likely_inflated_by": ["heat"], "interpretation": "x"})
    _seed_window(db, uid, n_easy=6, n_hard=2)

    read = build_context_pack(db, subject, prompt_id=GROUPED3).intensity_read
    assert read.confounders == ["heat"]
    assert read.drift_vs_typical is not None
    assert read.drift_vs_typical.confounded is True
