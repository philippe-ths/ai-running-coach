"""End-to-end: HTTP webhook → enqueued job → email captured.

Replaces RQ with an inline enqueuer that runs the captured job synchronously
inside the test session, using in-memory Strava and notifier adapters and a
patched AnthropicClient. This is the test the user pointed at in the design:
'simulate a webhook create event end-to-end and assert one email captured;
a second webhook for the same activity captures no further sends.'
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, StravaAccount, User, UserProfile
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
    monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "x")
    # Exercises the SINGLE-SHOT structured email pipeline; the active default now
    # tracks the feature-bearing message prompt (#424), so pin the structured one.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_report_v10")


def _seed(db, athlete_id: int = 70707):
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
        strava_athlete_id=athlete_id,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()


def _seed_strava(adapter, strava_activity_id: int = 1234567):
    adapter.seed_activities(
        [
            {
                "id": strava_activity_id,
                "name": "Easy",
                "type": "Run",
                "start_date": "2026-05-27T07:00:00Z",
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
        },
    )


def _valid_llm_json() -> str:
    return (
        '{'
        '"key_takeaways":['
        '{"text":"Stayed in zone 2."},'
        '{"text":"HR drift minimal."}'
        '],'
        '"next_steps":[{"action":"Strides","details":"4x20s","why":"Turnover"}]'
        '}'
    )


def _webhook_payload(*, aspect_type: str, activity_id: int, athlete_id: int):
    return {
        "object_type": "activity",
        "object_id": activity_id,
        "aspect_type": aspect_type,
        "owner_id": athlete_id,
        "subscription_id": 1,
        "event_time": 1700000000,
        "updates": {},
    }


def test_webhook_create_triggers_one_email_and_dedupes_on_replay(
    client, db, strava_adapter, notifier, configured
):
    _seed(db)
    _seed_strava(strava_adapter)

    captured: list[tuple] = []

    def inline_enqueue(func, **kwargs):
        captured.append((func, kwargs))

    fake_queue = AsyncMock()
    fake_queue.enqueue = inline_enqueue

    fake_client = AsyncMock()
    fake_client.generate_json = AsyncMock(return_value=_valid_llm_json())

    # Replace the queue.enqueue at the webhook handler import site and patch
    # the AnthropicClient inside the coach service.
    with patch("app.api.webhooks.queue", fake_queue), patch(
        "app.services.coach.service.AnthropicClient", return_value=fake_client
    ):
        # First webhook: should enqueue the pipeline job
        r1 = client.post(
            "/api/webhooks/strava",
            json=_webhook_payload(
                aspect_type="create", activity_id=1234567, athlete_id=70707
            ),
        )
        assert r1.status_code == 200
        assert r1.json()["action"] == "enqueued_pipeline"
        assert len(captured) == 1

        # Run the captured job inline against the test DB by calling the inner
        # async function directly with our shared session + ports.
        from app.jobs.process_new_activity import process_new_activity
        import asyncio

        account = (
            db.query(StravaAccount).filter_by(strava_athlete_id=70707).first()
        )
        sent_notif = asyncio.new_event_loop().run_until_complete(
            process_new_activity(
                db=db,
                account=account,
                strava_activity_id=1234567,
                strava_port=strava_adapter,
                notifier=notifier,
            )
        )

        assert sent_notif is not None
        assert len(notifier.sent) == 1
        assert notifier.sent[0].to == "runner@example.com"
        assert "8.2km" in notifier.sent[0].subject

        # Second webhook for same activity — handler still enqueues (RQ would
        # dedup via job_id; here we just simulate the pipeline running again
        # and assert dedup-via-notified_at kicks in).
        r2 = client.post(
            "/api/webhooks/strava",
            json=_webhook_payload(
                aspect_type="create", activity_id=1234567, athlete_id=70707
            ),
        )
        assert r2.status_code == 200
        sent_notif_2 = asyncio.new_event_loop().run_until_complete(
            process_new_activity(
                db=db,
                account=account,
                strava_activity_id=1234567,
                strava_port=strava_adapter,
                notifier=notifier,
            )
        )

    assert sent_notif_2 is None
    assert len(notifier.sent) == 1  # still one email total

    activity = db.query(Activity).filter_by(strava_activity_id=1234567).first()
    assert activity.coach_notification_sent_at is not None
