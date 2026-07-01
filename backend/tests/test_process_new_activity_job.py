"""End-to-end test for the activity-notification pipeline job."""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs.process_new_activity import process_new_activity
from app.models import Activity, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.services.notifications import InMemoryNotifier, set_notifier
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    set_strava_port,
)


@pytest.fixture(autouse=True)
def _pin_structured_prompt(monkeypatch):
    # This suite exercises the SINGLE-SHOT structured pipeline; the active default
    # now tracks the feature-bearing message prompt (#424), so pin the structured one.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


@pytest.fixture
def notifier():
    n = InMemoryNotifier()
    set_notifier(n)
    yield n
    set_notifier(None)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")


def _seed(db, *, strava_activity_id: int = 9001):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=12345,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user, account


def _seed_strava(adapter, strava_activity_id: int = 9001):
    adapter.seed_activities(
        [
            {
                "id": strava_activity_id,
                "name": "Morning easy run",
                "type": "Run",
                "start_date": "2026-05-27T08:00:00Z",
                "distance": 8200,
                "moving_time": 2400,
                "elapsed_time": 2400,
                "total_elevation_gain": 40,
                "average_heartrate": 142,
                "average_speed": 3.4,
            }
        ]
    )
    adapter.seed_streams(
        strava_activity_id,
        {
            "time": {"data": [0, 60, 120, 180]},
            "heartrate": {"data": [130, 140, 145, 148]},
            "distance": {"data": [0, 200, 400, 600]},
        },
    )


def _valid_llm_json() -> str:
    return (
        '{'
        '"key_takeaways":['
        '{"text":"Stayed in zone 2 throughout."},'
        '{"text":"HR drift was minimal."}'
        '],'
        '"next_steps":[{"action":"Add strides","details":"4x20s","why":"Improve turnover"}],'
        '"risks":[],'
        '"questions":[]'
        '}'
    )


@pytest.mark.asyncio
async def test_pipeline_dedupes_second_run(
    db, strava_adapter, notifier, configured
):
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )
        # Second invocation must not send a second notification
        result_2 = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result_2 is None
    assert len(notifier.sent) == 1


@pytest.mark.asyncio
async def test_pipeline_skips_notification_when_llm_fallback(
    db, strava_adapter, notifier, configured
):
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value="invalid json")

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        result = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result is None
    assert notifier.sent == []
    activity = db.query(Activity).filter_by(strava_activity_id=9001).first()
    assert activity.coach_notification_sent_at is None


@pytest.mark.asyncio
async def test_pipeline_sends_telegram_on_happy_path(
    db, strava_adapter, notifier, configured
):
    # Telegram is the only channel (#595): the notification is rendered
    # Telegram-shaped (link out of band, no HTML body).
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        result = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result is not None
    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.to == "42"
    assert "8.2km" in sent.subject
    assert "Stayed in zone 2 throughout." in sent.text
    # Telegram carries the activity link out of band, not embedded HTML.
    assert sent.html == ""
    assert sent.url == "http://localhost:3000/activity/" + str(
        db.query(Activity).filter_by(strava_activity_id=9001).first().id
    )

    activity = db.query(Activity).filter_by(strava_activity_id=9001).first()
    assert activity.coach_notification_sent_at is not None


@pytest.mark.asyncio
async def test_pipeline_send_failure_is_non_fatal_and_leaves_sentinel_null(
    db, strava_adapter, configured, monkeypatch
):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    class FailingNotifier:
        def send(self, notification):
            raise OSError("Network is unreachable")

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        # Must not raise: a transport failure is swallowed and logged.
        result = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=FailingNotifier(),
        )

    assert result is None
    activity = db.query(Activity).filter_by(strava_activity_id=9001).first()
    # Sentinel left null so the activity stays eligible for a later re-send.
    assert activity.coach_notification_sent_at is None
    # The coach report itself was still generated and persisted.
    report = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity.id)
        .first()
    )
    assert report is not None
    assert report.is_fallback is False


@pytest.mark.asyncio
async def test_pipeline_fallback_gate_uses_active_version_not_stale_row(
    db, strava_adapter, notifier, configured
):
    """Regression (N1): once version rows coexist, the notify gate must read the
    *active*-version report's is_fallback, not an arbitrary `.first()` row. A
    retained stale fallback row from a prior schema version must not suppress
    notification of a fresh, good active-version report."""
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    # First run produces a fallback report (invalid JSON); notification skipped.
    bad_client = AsyncMock()
    bad_client.generate_json = AsyncMock(return_value="invalid json")
    with patch("app.services.coach.service.AnthropicClient", return_value=bad_client):
        first = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )
    assert first is None
    activity = db.query(Activity).filter_by(strava_activity_id=9001).first()
    stale = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()
    assert stale.is_fallback is True
    # Demote it to a prior version: retained, and inserted before the fresh row,
    # so a version-unaware `.first()` would return it (lowest rowid in SQLite).
    stale.schema_version = "1.0"
    db.commit()
    stale_id = stale.id

    # Second run generates a fresh, good active-version report.
    good_client = AsyncMock()
    good_client.generate_json = AsyncMock(return_value=_valid_llm_json())
    with patch("app.services.coach.service.AnthropicClient", return_value=good_client):
        second = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert second is not None
    assert len(notifier.sent) == 1
    # Prior-version row retained alongside the fresh active one.
    assert db.query(CoachReport).filter(CoachReport.id == stale_id).first() is not None


@pytest.mark.asyncio
async def test_pipeline_skips_when_channel_unset(
    db, strava_adapter, notifier, monkeypatch
):
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        result = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result is None
    assert notifier.sent == []
