"""P1.1 Voice — service wiring: the resolved voice reaches the LLM system prompt.

Closes the coverage gap between 'build_system_prompt composes a voice block' (unit,
test_voice.py) and 'service.py actually threads the runner's declared voice into the
generation call'. Uses a stubbed Anthropic client (no API cost) and inspects the
`system` argument the service passes for the opener and fuller stages under
coach_message_v3.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.llm import MessageResult
from app.services.coach.service import generate_fuller, generate_opener
from app.services.coach.voice import PRESETS

pytestmark = pytest.mark.asyncio


def _seed(db) -> Activity:
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


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _ok_result():
    return MessageResult(
        content_blocks=[_text("Solid easy run, nicely controlled."), _tail(headline="Easy run")],
        stop_reason="end_turn",
    )


def _fake_client():
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_ok_result())
    return fake


async def test_fuller_threads_declared_voice_into_system_prompt(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)
    db.add(CoachingRelationship(user_id=activity.user_id, voice_preset="roast"))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.service.AnthropicClient", return_value=fake), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))

    system = fake.generate_coach_message.call_args.kwargs["system"]
    assert "## YOUR VOICE FOR THIS RUNNER" in system
    assert PRESETS["roast"].name in system
    # an example message rode along (the steering ingredient)
    assert PRESETS["roast"].example_messages[0][:30] in system


async def test_opener_threads_declared_voice_into_system_prompt(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)
    db.add(CoachingRelationship(user_id=activity.user_id, voice_preset="sage"))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.service.AnthropicClient", return_value=fake):
        await generate_opener(db, str(activity.id))

    system = fake.generate_coach_message.call_args.kwargs["system"]
    assert "## YOUR VOICE FOR THIS RUNNER" in system
    assert PRESETS["sage"].name in system


async def test_undeclared_voice_threads_default_block_under_v3(db, monkeypatch):
    # No CoachingRelationship row at all: voice resolves to the moderate default and
    # the default block is still rendered (so the coach speaks at the centre).
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)

    fake = _fake_client()
    with patch("app.services.coach.service.AnthropicClient", return_value=fake), \
         patch("app.services.coach.service.write_back_beliefs"), \
         patch("app.services.coach.service.enqueue_consolidation"):
        await generate_fuller(db, str(activity.id))

    system = fake.generate_coach_message.call_args.kwargs["system"]
    assert "default moderate coaching voice" in system
    # no preset example-messages section for the default voice
    assert "EXAMPLE MESSAGES (match the register" not in system
