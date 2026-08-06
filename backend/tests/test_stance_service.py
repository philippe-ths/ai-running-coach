"""P1.3 Stance — service wiring: the resolved stance reaches the generation call.

Closes the gap between 'build_context_pack threads a stance' (unit,
test_stance_context.py) and 'service.py actually resolves the runner's declared
stance and threads it into the opener + fuller calls'. Uses a stubbed Anthropic
client (no API cost) and inspects the `user` (the pack JSON) and `system` arguments
the service passes under coach_message_v5. Also covers the storage columns: a real
CoachingRelationship row carries the stance the resolver reads.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.corpus import DEFAULT_SCHOOL_ID
from app.services.coach.llm import MessageResult
from app.services.coach.service import (
    _resolve_stance_for_activity,
    generate_fuller,
    generate_opener,
)

V5 = "coach_message_v5"
V4 = "coach_message_v4"


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


def _fake_client():
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=MessageResult(
        content_blocks=[_text("Solid easy run, nicely controlled."), _tail(headline="Easy run")],
        stop_reason="end_turn",
    ))
    return fake


def _first_call(fake):
    # The ORIGINAL generation call always carries the pack as `user`. A policy
    # retry makes a SECOND call whose `user` is the rewrite instruction, so read
    # the first call to stay robust to a retry.
    return fake.generate_coach_message.call_args_list[0].kwargs


def _pack_from_call(fake) -> dict:
    return json.loads(_first_call(fake)["user"])


# ---------------------------------------------------------------------------
# Storage: the resolver reads a real CoachingRelationship row's stance columns
# ---------------------------------------------------------------------------


def test_resolver_reads_stored_stance_columns(db):
    activity = _seed(db)
    db.add(CoachingRelationship(
        user_id=activity.user_id, stance_school="periodization",
        stance_data_sentiment=2, stance_process_outcome=5,
    ))
    db.commit()
    stance = _resolve_stance_for_activity(db, activity)
    assert stance.school_id == "periodization"
    assert stance.emphasis.data_sentiment == 2
    assert stance.emphasis.process_outcome == 5
    assert stance.is_default is False


def test_resolver_defaults_when_no_relationship_row(db):
    activity = _seed(db)
    stance = _resolve_stance_for_activity(db, activity)
    assert stance.school_id == DEFAULT_SCHOOL_ID
    assert stance.is_default is True


# ---------------------------------------------------------------------------
# Threading: the resolved stance reaches the pack (user message) under v5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fuller_threads_declared_stance_into_pack(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V5)
    activity = _seed(db)
    db.add(CoachingRelationship(
        user_id=activity.user_id, stance_school="polarized",
        stance_data_sentiment=5, stance_process_outcome=1,
    ))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))

    pack = _pack_from_call(fake)
    # the runner's selected school rode into the corpus section
    assert pack["corpus"]["school"]["id"] == "polarized"
    # the emphasis axes rode into the stance section
    emphasis = {a["key"]: a["value"] for a in pack["stance"]["emphasis"]}
    assert emphasis == {"data_sentiment": 5, "process_outcome": 1}
    # the system prompt carries the rule-26 emphasis discipline (the static addendum)
    system = _first_call(fake)["system"]
    assert "COACHING STANCE — EMPHASIS" in system


@pytest.mark.asyncio
async def test_opener_threads_declared_stance_into_pack(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V5)
    activity = _seed(db)
    db.add(CoachingRelationship(
        user_id=activity.user_id, stance_school="enjoyment-and-consistency",
        stance_data_sentiment=4, stance_process_outcome=4,
    ))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_opener(db, str(activity.id))

    pack = _pack_from_call(fake)
    assert pack["corpus"]["school"]["id"] == "enjoyment-and-consistency"
    emphasis = {a["key"]: a["value"] for a in pack["stance"]["emphasis"]}
    assert emphasis == {"data_sentiment": 4, "process_outcome": 4}


@pytest.mark.asyncio
async def test_undeclared_stance_uses_default_school_and_balanced_under_v5(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V5)
    activity = _seed(db)  # no CoachingRelationship row

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))

    pack = _pack_from_call(fake)
    assert pack["corpus"]["school"]["id"] == DEFAULT_SCHOOL_ID
    assert all(a["value"] == 3 for a in pack["stance"]["emphasis"])


@pytest.mark.asyncio
async def test_under_v4_a_declared_stance_is_inert(db, monkeypatch):
    """Activation boundary: under v4 the runner's school does NOT apply and there is
    no stance section — P1.3 activates only with v5 (AC1)."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", V4)
    activity = _seed(db)
    db.add(CoachingRelationship(
        user_id=activity.user_id, stance_school="polarized",
        stance_data_sentiment=5, stance_process_outcome=1,
    ))
    db.commit()

    fake = _fake_client()
    with patch("app.services.coach.turn.AnthropicClient", return_value=fake):
        await generate_fuller(db, str(activity.id))

    pack = _pack_from_call(fake)
    assert pack["corpus"]["school"]["id"] == DEFAULT_SCHOOL_ID  # hardcoded default, NOT polarized
    assert "stance" not in pack
