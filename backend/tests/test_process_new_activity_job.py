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
    monkeypatch.setattr(settings, "NOTIFY_TO", "runner@example.com")
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")


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
async def test_pipeline_sends_email_on_happy_path(
    db, strava_adapter, notifier, configured
):
    _user, account = _seed(db)
    _seed_strava(strava_adapter)

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    with patch("app.services.coach.service.AnthropicClient", return_value=fake_client):
        result = await process_new_activity(
            db=db,
            account=account,
            strava_activity_id=9001,
            strava_port=strava_adapter,
            notifier=notifier,
        )

    assert result is not None
    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.to == "runner@example.com"
    assert "8.2km" in sent.subject
    assert "Stayed in zone 2 throughout." in sent.text
    assert "Stayed in zone 2 throughout." in sent.html

    activity = db.query(Activity).filter_by(strava_activity_id=9001).first()
    assert activity.coach_notification_sent_at is not None


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
        # Second invocation must not send a second email
        result_2 = await process_new_activity(
            db=db, account=account, strava_activity_id=9001,
            strava_port=strava_adapter, notifier=notifier,
        )

    assert result_2 is None
    assert len(notifier.sent) == 1


@pytest.mark.asyncio
async def test_pipeline_skips_email_when_llm_fallback(
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
    db, strava_adapter, notifier, configured, monkeypatch
):
    # Telegram configured on top of the email settings: the Telegram channel
    # takes priority, so the notification is rendered Telegram-shaped.
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
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
async def test_pipeline_skips_when_notify_to_unset(
    db, strava_adapter, notifier, monkeypatch
):
    monkeypatch.setattr(settings, "NOTIFY_TO", "")
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
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
