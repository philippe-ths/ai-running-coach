"""Verify that the webhook handler dispatches the right job per aspect_type."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_queue():
    fake = MagicMock()
    with patch("app.api.webhooks.queue", fake):
        yield fake


def _post(client, *, aspect_type: str, object_id: int = 7777):
    payload = {
        "object_type": "activity",
        "object_id": object_id,
        "aspect_type": aspect_type,
        "owner_id": 999,
        "subscription_id": 1,
        "event_time": 1700000000,
        "updates": {},
    }
    return client.post("/api/webhooks/strava", json=payload)


def test_create_enqueues_process_new_activity_job(client, fake_queue):
    from app.jobs.process_new_activity import process_new_activity_job

    response = _post(client, aspect_type="create")
    assert response.status_code == 200
    assert response.json()["status"] == "processed"

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] is process_new_activity_job
    assert kwargs["strava_athlete_id"] == 999
    assert kwargs["strava_activity_id"] == 7777


def test_update_enqueues_existing_sync_activity_job(client, fake_queue):
    from app.jobs.strava_sync import sync_activity_job

    response = _post(client, aspect_type="update")
    assert response.status_code == 200

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] is sync_activity_job


def test_delete_does_not_enqueue(client, fake_queue):
    response = _post(client, aspect_type="delete")
    assert response.status_code == 200
    fake_queue.enqueue.assert_not_called()
