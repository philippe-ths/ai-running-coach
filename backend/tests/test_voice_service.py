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
from app.models import Activity, CoachReport, DerivedMetric, StravaAccount, User, UserProfile
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


def _stored_report(db, activity) -> dict:
    """The report JSON actually persisted for this activity."""
    db.expire_all()
    row = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity.id)
        .order_by(CoachReport.created_at.desc())
        .first()
    )
    return row.report


def _ok_result():
    return MessageResult(
        content_blocks=[_text("Solid easy run, nicely controlled."), _tail(headline="Easy run")],
        stop_reason="end_turn",
    )


# The re-voiced text a stubbed rewrite returns. Deliberately free of digits: the
# rewrite stage rejects any number the baseline did not contain, so a voiced
# fixture that invented one would be silently discarded and the test would pass
# for the wrong reason.
_VOICED = "Mate, that was a tidy little run and you kept it honest throughout."


def _fake_client(voiced: str = _VOICED):
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_ok_result())
    # Usage is None so the budget recorder skips it; spend metering has its own tests.
    fake.generate_json_with_usage = AsyncMock(return_value=(voiced, None))
    return fake


async def test_fuller_generates_voiceless_then_revoices(db, monkeypatch):
    """#822: the runner's voice reaches the REWRITE, never the generation.

    Asserting both halves together is the point — a voice that leaked into the
    system prompt would be steering the coach's judgment, which is the failure
    this architecture exists to make impossible.
    """
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)
    db.add(CoachingRelationship(user_id=activity.user_id, voice_preset="roast"))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))

    generation = fake.generate_coach_message.call_args.kwargs["system"]
    assert "## YOUR VOICE FOR THIS RUNNER" not in generation
    assert PRESETS["roast"].name not in generation

    rewrite = fake.generate_json_with_usage.call_args.kwargs["system"]
    assert PRESETS["roast"].name in rewrite
    assert PRESETS["roast"].example_messages[0][:30] in rewrite

    report = _stored_report(db, activity)
    assert report["voiced_message"] == _VOICED
    # The baseline survives underneath, which is what makes the voice auditable.
    assert report["message"] == "Solid easy run, nicely controlled."


async def test_opener_revoices_its_own_prose(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)
    db.add(CoachingRelationship(user_id=activity.user_id, voice_preset="sage"))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_opener(db, str(activity.id))

    assert "## YOUR VOICE FOR THIS RUNNER" not in (
        fake.generate_coach_message.call_args.kwargs["system"]
    )
    assert PRESETS["sage"].name in fake.generate_json_with_usage.call_args.kwargs["system"]
    # The opener's voiced prose has its own field: a two-stage exchange evolves ONE
    # row, so sharing a field would let the fuller turn overwrite the opener.
    report = _stored_report(db, activity)
    assert report["voiced_opener_message"] == _VOICED
    assert report["voiced_message"] is None


async def test_undeclared_voice_runs_no_rewrite(db, monkeypatch):
    """Default is genuinely off: no second call, and no voiced text stored.

    With no CoachingRelationship row the voice resolves to the moderate default,
    which is the runner declining to choose — so they get the baseline, and are
    not charged a model call to be told the same thing.
    """
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v3")
    activity = _seed(db)

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))

    assert fake.generate_json_with_usage.await_count == 0
    assert _stored_report(db, activity)["voiced_message"] is None
