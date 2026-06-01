"""Verify webhook event authentication and per-aspect_type job dispatch.

Strava does not sign webhook payloads (#100), so the handler authenticates
each event against the connected athlete and (when configured) the active
subscription id before doing any work. These tests cover both the dispatch
behaviour for authentic events and the rejection of forged ones.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, StravaAccount, User

# The athlete id that the seeded connected account owns. Authentic events in
# these tests carry this owner_id; forged events carry a different one.
CONNECTED_ATHLETE_ID = 999


@pytest.fixture
def fake_queue():
    fake = MagicMock()
    with patch("app.api.webhooks.queue", fake):
        yield fake


@pytest.fixture
def connected_account(db):
    """Seed the single connected athlete so authentic events pass the owner
    check. Without this, every event is rejected as an unknown owner."""
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=CONNECTED_ATHLETE_ID,
        access_token="test-token-do-not-use-in-prod",
        refresh_token="test-refresh-do-not-use-in-prod",
        expires_at=9999999999,
        scope="read",
    )
    db.add(account)
    db.commit()
    return account


def _post(
    client,
    *,
    aspect_type: str,
    object_id: int = 7777,
    owner_id: int = CONNECTED_ATHLETE_ID,
    subscription_id: int = 1,
):
    payload = {
        "object_type": "activity",
        "object_id": object_id,
        "aspect_type": aspect_type,
        "owner_id": owner_id,
        "subscription_id": subscription_id,
        "event_time": 1700000000,
        "updates": {},
    }
    return client.post("/api/webhooks/strava", json=payload)


class TestDispatchForAuthenticEvents:
    def test_create_enqueues_process_new_activity_job(
        self, client, fake_queue, connected_account
    ):
        from app.jobs.process_new_activity import process_new_activity_job

        response = _post(client, aspect_type="create")
        assert response.status_code == 200
        assert response.json()["status"] == "processed"

        fake_queue.enqueue.assert_called_once()
        args, kwargs = fake_queue.enqueue.call_args
        assert args[0] is process_new_activity_job
        assert kwargs["strava_athlete_id"] == CONNECTED_ATHLETE_ID
        assert kwargs["strava_activity_id"] == 7777

    def test_update_enqueues_existing_sync_activity_job(
        self, client, fake_queue, connected_account
    ):
        from app.jobs.strava_sync import sync_activity_job

        response = _post(client, aspect_type="update")
        assert response.status_code == 200

        fake_queue.enqueue.assert_called_once()
        args, kwargs = fake_queue.enqueue.call_args
        assert args[0] is sync_activity_job

    def test_delete_does_not_enqueue(self, client, fake_queue, connected_account):
        response = _post(client, aspect_type="delete")
        assert response.status_code == 200
        fake_queue.enqueue.assert_not_called()


class TestRejectsForgedEvents:
    """Threat model: an attacker who learns the callback URL POSTs a forged
    but well-formed event. The attacker must not be able to enqueue a job or
    mutate any activity."""

    def test_unknown_owner_is_rejected_without_enqueue(
        self, client, fake_queue, connected_account
    ):
        # No connected account owns athlete 12345.
        response = _post(client, aspect_type="create", owner_id=12345)
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_no_connected_account_rejects_everything(self, client, fake_queue):
        # Nothing seeded: there is no connected athlete, so no event is authentic.
        response = _post(client, aspect_type="create")
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_subscription_mismatch_is_rejected_without_enqueue(
        self, client, fake_queue, connected_account, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_SUBSCRIPTION_ID", 350809)
        # Authentic owner, but the event references a different subscription.
        response = _post(client, aspect_type="create", subscription_id=1)
        assert response.status_code == 403
        fake_queue.enqueue.assert_not_called()

    def test_matching_subscription_and_owner_is_accepted(
        self, client, fake_queue, connected_account, monkeypatch
    ):
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_SUBSCRIPTION_ID", 350809)
        response = _post(client, aspect_type="create", subscription_id=350809)
        assert response.status_code == 200
        fake_queue.enqueue.assert_called_once()

    def test_forged_delete_does_not_soft_delete_activity(self, client, db, fake_queue):
        """The delete branch soft-deletes by object_id. A forged delete event
        must not be able to remove an activity it does not own."""
        from datetime import datetime

        user = User(email=f"u-{uuid4()}@example.com")
        db.add(user)
        db.commit()
        activity = Activity(
            user_id=user.id,
            strava_activity_id=5555,
            start_date=datetime(2026, 5, 27, 10, 0, 0),
            type="Run",
            name="Victim run",
            distance_m=5000,
            moving_time_s=1500,
            elapsed_time_s=1500,
        )
        db.add(activity)
        db.commit()

        # Forged delete from an owner with no connected account.
        response = _post(client, aspect_type="delete", object_id=5555, owner_id=12345)
        assert response.status_code == 403

        db.refresh(activity)
        assert activity.is_deleted is False
