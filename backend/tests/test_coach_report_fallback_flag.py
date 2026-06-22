"""Tests that CoachReport.is_fallback is set correctly when the LLM fails."""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import anthropic
import httpx
import pytest

from app.models import (
    Activity,
    DerivedMetric,
    StravaAccount,
    User,
    UserProfile,
)
from app.models.coach_report import CoachReport
from app.services.coach.service import get_or_generate_coach_report


@pytest.fixture(autouse=True)
def _pin_structured_prompt(monkeypatch):
    # This suite exercises the STRUCTURED single-shot path; the active default now
    # tracks the feature-bearing message prompt (#424), so pin the structured one.
    from app.core.config import settings

    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")


def _seed_activity_with_metrics(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()

    profile = UserProfile(
        user_id=user.id,
        goal_type="general",
        experience_level="intermediate",
        weekly_days_available=4,
        max_hr=190,
    )
    db.add(profile)

    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=1,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read",
    )
    db.add(account)

    activity = Activity(
        user_id=user.id,
        strava_activity_id=42,
        start_date=datetime(2026, 5, 27, 10, 0, 0),
        type="Run",
        name="Test run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10.0,
        avg_hr=140,
        raw_summary={},
    )
    db.add(activity)
    db.commit()

    # Minimal DerivedMetric so get_or_generate_coach_report has metrics to read
    metric = DerivedMetric(
        activity_id=activity.id,
        effort="easy",
        structure="continuous",
        duration_class="standard",
        effort_score=50.0,
        flags=[],
        confidence="medium",
        confidence_reasons=[],
    )
    db.add(metric)
    db.commit()
    db.refresh(activity)
    return activity


@pytest.mark.asyncio
async def test_is_fallback_true_when_llm_returns_invalid_json(db, monkeypatch):
    activity = _seed_activity_with_metrics(db)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value="not valid json at all")

    with patch(
        "app.services.coach.service.AnthropicClient", return_value=fake_client
    ):
        report = await get_or_generate_coach_report(db, str(activity.id))

    assert report is not None
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored is not None
    assert stored.is_fallback is True


@pytest.mark.asyncio
async def test_is_fallback_false_on_happy_path(db):
    activity = _seed_activity_with_metrics(db)

    valid_json = (
        '{"key_takeaways":['
        '{"text":"You ran well."},'
        '{"text":"Heart rate was steady."}'
        '],'
        '"next_steps":[{"action":"Keep going","details":"Stay easy","why":"Build base"}]'
        '}'
    )
    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=valid_json)

    with patch(
        "app.services.coach.service.AnthropicClient", return_value=fake_client
    ):
        report = await get_or_generate_coach_report(db, str(activity.id))

    assert report is not None
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored is not None
    assert stored.is_fallback is False


@pytest.mark.asyncio
async def test_is_fallback_true_when_llm_transport_error_propagates(db):
    """After the LLM client's retry is exhausted, the underlying
    anthropic.APIError propagates. The coach service must route that to the
    same is_fallback=True path as JSONDecodeError / ValidationError so the
    pipeline job does not die on a transient outage."""
    activity = _seed_activity_with_metrics(db)

    timeout_err = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(side_effect=timeout_err)

    with patch(
        "app.services.coach.service.AnthropicClient", return_value=fake_client
    ):
        report = await get_or_generate_coach_report(db, str(activity.id))

    assert report is not None
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored is not None
    assert stored.is_fallback is True
