"""A4 salience substrate in the coach context pack (DB-integration).

Confirms build_context_pack gathers the runner's axis history + flags/pain into
the pure novelty and safety-override functions and surfaces them on the new
`salience` section, and that the fuller-turn `continuity` section carries the
opener prose + a chat reply.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric
from app.models.checkin import CheckIn
from app.models.coach_chat_message import CoachChatMessage
from app.models.user import User
from app.schemas.coach_context import ContinuityContext
from app.services.coach.context import build_context_pack
from app.services.coach.retrieval import fetch_latest_user_reply


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, uid, day):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 3, day, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={"average_temp": 15.0},
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, act, *, structure="continuous", duration_class="standard",
             is_race=False, is_hilly=False, flags=None):
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id, effort="easy",
        duration_class=duration_class, structure=structure, is_hilly=is_hilly,
        is_race=is_race, effort_score=3.0, hr_drift=4.0, confidence="high",
        confidence_reasons=[], flags=flags or [],
    ))
    db.flush()


def _checkin(db, act, *, pain_score, pain_location="knee"):
    db.add(CheckIn(id=uuid.uuid4(), activity_id=act.id,
                   pain_score=pain_score, pain_location=pain_location))
    db.flush()


# --- novelty ------------------------------------------------------------------


def test_novelty_first_interval_session(db):
    uid = _user(db)
    for d in (1, 2, 3, 4):
        _metrics(db, _activity(db, uid, d))  # prior easy continuous runs
    current = _activity(db, uid, 6)
    _metrics(db, current, structure="intervals")

    salience = build_context_pack(db, current).salience
    assert salience.novelty.has_history is True
    assert "first_interval_session" in salience.novelty.first_of_kind


def test_novelty_abstains_on_thin_history(db):
    uid = _user(db)
    for d in (1, 2):  # only two priors (< MIN_HISTORY_FOR_NOVELTY)
        _metrics(db, _activity(db, uid, d))
    current = _activity(db, uid, 4)
    _metrics(db, current, structure="intervals")

    salience = build_context_pack(db, current).salience
    assert salience.novelty.has_history is False
    assert salience.novelty.first_of_kind == []


def test_novelty_not_first_when_prior_interval_exists(db):
    uid = _user(db)
    for d in (1, 2, 3):
        _metrics(db, _activity(db, uid, d))
    _metrics(db, _activity(db, uid, 4), structure="intervals")  # a prior interval session
    current = _activity(db, uid, 6)
    _metrics(db, current, structure="intervals")

    salience = build_context_pack(db, current).salience
    assert "first_interval_session" not in salience.novelty.first_of_kind


def test_novelty_empty_for_unremarkable_run(db):
    uid = _user(db)
    for d in (1, 2, 3, 4):
        _metrics(db, _activity(db, uid, d))
    current = _activity(db, uid, 6)
    _metrics(db, current)

    salience = build_context_pack(db, current).salience
    assert salience.novelty.first_of_kind == []


# --- safety override ----------------------------------------------------------


def test_safety_override_fires_on_illness_flag(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current, flags=["illness_or_extreme_fatigue"])

    override = build_context_pack(db, current).salience.safety_override
    assert override.force_fuller is True
    assert override.reasons == ["multi_signal_fatigue"]


def test_safety_override_fires_on_sustained_pain(db):
    uid = _user(db)
    # three recent runs with notable pain (>= 4) in the same window
    for d in (1, 3, 5):
        a = _activity(db, uid, d)
        _metrics(db, a)
        _checkin(db, a, pain_score=5)
    current = _activity(db, uid, 6)
    _metrics(db, current)
    _checkin(db, current, pain_score=5)

    override = build_context_pack(db, current).salience.safety_override
    assert override.force_fuller is True
    assert override.reasons == ["persistent_pain"]


def test_safety_override_abstains_on_clean_run(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current, flags=["high_drift"])

    override = build_context_pack(db, current).salience.safety_override
    assert override.force_fuller is False
    assert override.reasons == []


# --- continuity ---------------------------------------------------------------


def test_continuity_empty_by_default(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current)

    continuity = build_context_pack(db, current).continuity
    assert continuity.opener_message is None
    assert continuity.reply is None


def test_continuity_carries_opener_and_reply(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current)
    cont = ContinuityContext(opener_message="Nice work — more soon.", reply="felt easy")

    pack = build_context_pack(db, current, continuity=cont)
    assert pack.continuity.opener_message == "Nice work — more soon."
    assert pack.continuity.reply == "felt easy"


def test_fetch_latest_user_reply(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current)
    # Explicit, distinct timestamps: in production chat replies have distinct
    # created_at; the in-memory test shares a transaction clock, so set them.
    base = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    db.add(CoachChatMessage(id=uuid.uuid4(), activity_id=current.id,
                            role="user", content="first reply",
                            created_at=base))
    db.add(CoachChatMessage(id=uuid.uuid4(), activity_id=current.id,
                            role="assistant", content="coach reply",
                            created_at=base.replace(minute=5)))
    db.add(CoachChatMessage(id=uuid.uuid4(), activity_id=current.id,
                            role="user", content="latest reply",
                            created_at=base.replace(minute=10)))
    db.flush()

    assert fetch_latest_user_reply(db, current.id) == "latest reply"


def test_fetch_latest_user_reply_none_when_no_chat(db):
    uid = _user(db)
    current = _activity(db, uid, 1)
    _metrics(db, current)
    assert fetch_latest_user_reply(db, current.id) is None
