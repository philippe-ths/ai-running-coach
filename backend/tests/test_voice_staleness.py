"""Voice freshness for coach reports (P1.1 follow-up).

A coach report stamps the voice it was generated under (`voice_key`); a later voice
change makes the active-version report read as STALE so it regenerates onto the new
voice. These tests cover the read-side predicate `report_voice_stale` directly (no
LLM) and assert the generate path actually stamps the fingerprint.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.llm import MessageResult
from app.services.coach.receipt_voice import voice_fingerprint
from app.services.coach.service import (
    active_schema_version,
    generate_opener,
    get_active_report_row,
    report_voice_stale,
)
from app.services.coach.voice import resolve_voice


# --- seeding ------------------------------------------------------------------


def _seed_activity(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Test run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _set_voice(db, activity, **dials) -> CoachingRelationship:
    rel = CoachingRelationship(user_id=activity.user_id, **dials)
    db.add(rel)
    db.commit()
    return rel


def _seed_report(db, activity, *, prompt_id, schema_version, voice_key) -> CoachReport:
    row = CoachReport(
        activity_id=activity.id, prompt_id=prompt_id, schema_version=schema_version,
        report={}, meta={}, context_pack={}, voice_key=voice_key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- report_voice_stale (read-side predicate) ---------------------------------


def test_stale_when_voice_key_differs_under_voice_prompt(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")  # a declared, non-default voice
    current = voice_fingerprint(resolve_voice(db.query(CoachingRelationship).first()))
    row = _seed_report(
        db, activity, prompt_id="coach_message_v3",
        schema_version=active_schema_version("coach_message_v3"),
        voice_key="some-other-voice",
    )
    assert current != "some-other-voice"
    assert report_voice_stale(db, row) is True


def test_not_stale_when_voice_key_matches(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")
    current = voice_fingerprint(resolve_voice(db.query(CoachingRelationship).first()))
    row = _seed_report(
        db, activity, prompt_id="coach_message_v3",
        schema_version=active_schema_version("coach_message_v3"),
        voice_key=current,
    )
    assert report_voice_stale(db, row) is False


def test_null_voice_key_reads_as_stale_on_active_voice_prompt(db, monkeypatch):
    """The bug case: a report written before voice_key existed (NULL) regenerates
    once onto the current voice rather than silently keeping the old one."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")
    row = _seed_report(
        db, activity, prompt_id="coach_message_v3",
        schema_version=active_schema_version("coach_message_v3"),
        voice_key=None,
    )
    assert report_voice_stale(db, row) is True


def test_default_voice_is_not_stale(db, monkeypatch):
    """An undeclared (default) voice stamps the 'default' sentinel; a report carrying
    it is not stale, so we never burn a regen on the undeclared centre."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)  # no relationship => default voice
    row = _seed_report(
        db, activity, prompt_id="coach_message_v3",
        schema_version=active_schema_version("coach_message_v3"),
        voice_key="default",
    )
    assert report_voice_stale(db, row) is False


def test_non_voice_prompt_is_never_stale(db, monkeypatch):
    """Under a non-voice-aware prompt the voice block is inert, so voice can never
    make a report stale even if the stored key differs."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")
    row = _seed_report(
        db, activity, prompt_id="coach_report_v10",
        schema_version=active_schema_version("coach_report_v10"),
        voice_key="anything",
    )
    assert report_voice_stale(db, row) is False


def test_cross_version_fallback_left_alone(db, monkeypatch):
    """A displayable row from a DIFFERENT prompt version (#261) is served as-is and
    never voice-regenerated — that path must not storm regens after a prompt flip."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")
    row = _seed_report(  # an older prompt's report
        db, activity, prompt_id="coach_message_v2", schema_version="2.0",
        voice_key=None,
    )
    assert report_voice_stale(db, row) is False


# --- generate path stamps voice_key -------------------------------------------


def _opener_result():
    blocks = [
        {"type": "text", "text": "Nice work getting that one done!"},
        {"type": "tool_use", "name": "record_coach_tail", "input": {
            "headline": "Quick reaction", "next_steps": [], "risks": [],
            "questions": [], "schedule_fuller_turn": False,
        }},
    ]
    return MessageResult(content_blocks=blocks, stop_reason="end_turn")


@pytest.mark.asyncio
async def test_generate_stamps_current_voice_fingerprint(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed_activity(db)
    _set_voice(db, activity, voice_preset="roast")
    expected = voice_fingerprint(resolve_voice(db.query(CoachingRelationship).first()))

    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_opener_result())
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_opener(db, str(activity.id))

    row = get_active_report_row(db, activity.id)
    assert row is not None
    assert row.voice_key == expected
    assert report_voice_stale(db, row) is False
