"""A2a: a non-fallback CoachReport stores its exchange digest at write time;
a fallback report stores none. (Projection equality is pinned in
test_report_digest.py; this pins the service write seam.)
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.coach.llm import Usage

from app.models import Activity, DerivedMetric, User, UserProfile
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
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 10**9),
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


@pytest.mark.asyncio
async def test_non_fallback_report_stores_digest(db):
    activity = _seed_activity_with_metrics(db)

    valid_json = (
        '{"headline":"Steady easy run",'
        '"lead_argument":{"text":"Aerobic base is holding"},'
        '"key_takeaways":[{"text":"You ran well."},{"text":"HR was steady."}],'
        '"next_steps":[{"action":"Keep going","details":"Stay easy","why":"Build base"}]}'
    )
    fake_client = AsyncMock()
    fake_client.generate_json_with_usage = AsyncMock(
        return_value=(valid_json, Usage())
    )

    with patch("app.services.coach.turn.AnthropicClient", return_value=fake_client):
        report = await get_or_generate_coach_report(db, str(activity.id))

    assert report is not None
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored.is_fallback is False
    assert stored.digest is not None
    assert stored.digest["headline"] == "Steady easy run"
    assert stored.digest["lead_argument"] == "Aerobic base is holding"
    assert stored.digest["next_steps"] == ["Keep going (Stay easy)"]
    assert stored.digest["activity_date"] == activity.start_date.isoformat()


@pytest.mark.asyncio
async def test_fallback_report_stores_no_digest(db):
    activity = _seed_activity_with_metrics(db)

    fake_client = AsyncMock()
    fake_client.generate_json_with_usage = AsyncMock(
        return_value=("not valid json at all", Usage())
    )

    with patch("app.services.coach.turn.AnthropicClient", return_value=fake_client):
        report = await get_or_generate_coach_report(db, str(activity.id))

    assert report is not None
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored.is_fallback is True
    assert stored.digest is None
