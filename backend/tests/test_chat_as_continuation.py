"""I2: a conversational turn is a continuation of the coaching relationship.

The coach the runner talks to must speak in their declared Voice and carry the
same relationship-memory authority disciplines the report coach carries, so it is
the SAME coach they already heard from (epic #177, I2).

Driven through the thread turn, the surviving conversational surface (#770). The
thread assembles a relationship baseline rather than a stored report pack, so the
tiers it briefs are the ones that baseline actually carries — the gate is the
same shared renderer either way.
"""

from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings
from app.models import Activity, StravaAccount, User, UserProfile
from app.models.coaching_relationship import CoachingRelationship
from app.models.runner_memory import RunnerMemory
from app.models.thread import Thread
from app.services.coach.thread_turn import build_thread_system_prompt

VOICE_AWARE_PROMPT = "coach_message_v7"
NON_VOICE_PROMPT = "coach_report_v10"


def _seed(db, *, voice_preset=None, with_memory=False):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=1, access_token="t", refresh_token="r",
        expires_at=9999999999, scope="read",
    ))
    if voice_preset is not None:
        db.add(CoachingRelationship(user_id=user.id, voice_preset=voice_preset))
    if with_memory:
        db.add(RunnerMemory(
            user_id=user.id,
            profile={"who_you_are": ["a marathoner"]},
            model_id="claude-haiku-4-5",
            source_report_count=3,
        ))
    activity = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 10**9),
        start_date=datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc), type="Run",
        name="Test run", distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
        elev_gain_m=10.0, avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    thread = Thread(user_id=user.id, activity_id=activity.id)
    db.add(thread)
    db.commit()
    return user, thread


def test_prompt_carries_relationship_memory_disciplines(db):
    """#667 gates each tier on what is actually in front of the coach. With the
    runner's memory profile in the baseline, its discipline is briefed, and the
    tiers retired by ADR 0025 are never re-briefed."""
    user, thread = _seed(db, with_memory=True)
    prompt = build_thread_system_prompt(db, user, thread)

    assert "AUTHORITY TIERING" in prompt
    assert "memory" in prompt
    # retired tiers (ADR 0025 replaced narrative + believed_facts with the profile)
    assert "believed_facts" not in prompt
    assert "narrative" not in prompt.lower()
    # measured data and the safety floor are the top tier
    assert "safety floor" in prompt.lower()


def test_a_tier_is_not_briefed_when_its_data_is_absent(db):
    """The floor header always stands; a tier the baseline does not carry is not
    advertised, so the coach is never told to honour something it cannot see."""
    user, thread = _seed(db)
    prompt = build_thread_system_prompt(db, user, thread)

    assert "AUTHORITY TIERING" in prompt
    assert "- MEMORY (" not in prompt
    assert "- COACHING CORPUS" not in prompt


def test_prompt_speaks_in_the_declared_voice(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", VOICE_AWARE_PROMPT)
    user, thread = _seed(db, voice_preset="cornerman")
    prompt = build_thread_system_prompt(db, user, thread)

    assert "## YOUR VOICE FOR THIS RUNNER" in prompt
    assert "PRESET:" in prompt


def test_undeclared_runner_gets_the_moderate_default_voice(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", VOICE_AWARE_PROMPT)
    user, thread = _seed(db, voice_preset=None)
    prompt = build_thread_system_prompt(db, user, thread)

    assert "## YOUR VOICE FOR THIS RUNNER" in prompt
    assert "default moderate coaching voice" in prompt


def test_voice_gating_mirrors_the_report(db, monkeypatch):
    """No voice-aware active prompt, no voice block — the same gate the report
    honours, so the two surfaces cannot drift apart on voice."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", NON_VOICE_PROMPT)
    user, thread = _seed(db, voice_preset="cornerman")
    prompt = build_thread_system_prompt(db, user, thread)

    assert "## YOUR VOICE FOR THIS RUNNER" not in prompt
    assert "AUTHORITY TIERING" in prompt  # the floor header stays
    assert "- VOICE (" not in prompt


def test_voice_kill_switch_is_off_everywhere(db, monkeypatch):
    """#522/#668: the switch lives inside the shared render_voice_block, so a
    disabled voice is off on the conversational path too, not just the report."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", VOICE_AWARE_PROMPT)
    monkeypatch.setattr(settings, "COACH_VOICE_BLOCK_ENABLED", False)
    user, thread = _seed(db, voice_preset="cornerman")
    prompt = build_thread_system_prompt(db, user, thread)

    assert "## YOUR VOICE FOR THIS RUNNER" not in prompt
