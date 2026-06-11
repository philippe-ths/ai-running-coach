"""Regression for the legacy structured path truncation/fence bug under sonnet-4-6.

claude-sonnet-4-6 emits more verbose JSON (often markdown-fenced) than the retired
model. At max_tokens=1024 the report was truncated mid-object, dropping the closing
fence, and the partial fenced text (an opening json fence then a half-written object)
failed json.loads as "Expecting value: line 1 column 1" — silently falling back. The
fix raises the structured budget
(_STRUCTURED_MAX_TOKENS) and makes _strip_code_fences tolerant of an unterminated
opening fence. These pin the parse-recovery; the token bump is a constant.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.services.coach.service import _strip_code_fences, get_or_generate_coach_report


class TestStripCodeFences:
    def test_fully_fenced(self):
        assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_unterminated_opening_fence(self):
        # The truncation case: opening fence, valid JSON, no closing fence.
        assert _strip_code_fences('```json\n{"a": 1}') == '{"a": 1}'

    def test_bare_opening_fence_no_json_tag(self):
        assert _strip_code_fences('```\n{"a": 1}') == '{"a": 1}'

    def test_no_fence_passthrough(self):
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_trailing_fence_only(self):
        assert _strip_code_fences('{"a": 1}\n```') == '{"a": 1}'


def _seed(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate",
                       weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=1, access_token="t",
                         refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(user_id=user.id, strava_activity_id=42,
                        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
                        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500,
                        elev_gain_m=10.0, avg_hr=140, raw_summary={})
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(activity_id=activity.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=[],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(activity)
    return activity


@pytest.mark.asyncio
async def test_fence_no_close_recovers_to_non_fallback(db):
    """A valid structured JSON wrapped in an unterminated opening fence (the
    truncation signature) must parse, not fall back."""
    activity = _seed(db)
    fenced = (
        '```json\n'
        '{"key_takeaways":[{"text":"You ran well."},{"text":"HR was steady."}],'
        '"next_steps":[{"action":"Keep going","details":"Stay easy","why":"Build base"}]}'
    )
    fake = AsyncMock()
    fake.generate_json = AsyncMock(return_value=fenced)
    with patch("app.services.coach.service.AnthropicClient", return_value=fake):
        await get_or_generate_coach_report(db, str(activity.id))
    stored = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stored.is_fallback is False
    assert stored.report["key_takeaways"][0]["text"] == "You ran well."
