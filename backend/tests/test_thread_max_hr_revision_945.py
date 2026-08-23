"""#945: the max-HR revision detector reaches the thread baseline as a FACT,
never as an instruction to nag.

The report never sees this at all (#945 decisions 3/4 -- it is thread-only), so
these tests exercise `build_thread_system_prompt` directly rather than the
coach-report context pack.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.models.thread import Thread
from app.services.coach.thread_turn import build_thread_system_prompt

NOW = datetime.now(timezone.utc)


def _seed(db, *, max_hr=180):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=max_hr,
        )
    )
    db.commit()
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()
    return user, thread


def _activity(db, user, *, days_ago, max_hr):
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=NOW - timedelta(days=days_ago),
        type="Run",
        name="Run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=0.0,
        max_hr=max_hr,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    return activity


def test_no_evidence_means_no_section_in_the_prompt(db):
    user, thread = _seed(db)
    prompt = build_thread_system_prompt(db, user, thread)
    assert "MAX HEART RATE" not in prompt


def test_missing_profile_row_never_crashes_the_prompt_build(db):
    """A user with no UserProfile row at all (edge case, not the common path)
    must degrade to no section, not raise."""
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()

    prompt = build_thread_system_prompt(db, user, thread)

    assert "MAX HEART RATE" not in prompt


def test_qualifying_evidence_is_told_as_a_fact_with_an_offer_instruction(db):
    user, thread = _seed(db, max_hr=180)
    _activity(db, user, days_ago=1, max_hr=193)
    _activity(db, user, days_ago=5, max_hr=190)
    _activity(db, user, days_ago=10, max_hr=175)

    prompt = build_thread_system_prompt(db, user, thread)

    assert "MAX HEART RATE" in prompt
    assert "193" in prompt
    assert "180" in prompt
    # Told to OFFER the confirmable action, and explicitly warned off claiming
    # the write has already happened -- the North Star's "could an LLM misread
    # it" question applied to a fact that sits one tool call away from a write.
    assert "revise_max_hr" in prompt or "OFFER" in prompt
    assert "never" in prompt.lower() and "already" in prompt.lower()


def test_a_single_exceedance_never_reaches_the_prompt(db):
    """The pure-logic abstain cases are covered in test_max_hr_calibration_945;
    this pins that the wiring actually abstains end to end through the real
    prompt build, not only through the detector in isolation."""
    user, thread = _seed(db, max_hr=180)
    _activity(db, user, days_ago=1, max_hr=193)
    _activity(db, user, days_ago=5, max_hr=170)
    _activity(db, user, days_ago=10, max_hr=172)

    prompt = build_thread_system_prompt(db, user, thread)

    assert "MAX HEART RATE" not in prompt
