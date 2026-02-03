import pytest
from app.core.config import settings

@pytest.fixture
def test_webhook_token(monkeypatch):
    token = "test_verify_token"
    monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", token)
    return token

def test_webhook_verification_success(client, test_webhook_token):
    response = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": test_webhook_token,
            "hub.challenge": "challenge_code"
        }
    )
    assert response.status_code == 200
    assert response.json() == {"hub.challenge": "challenge_code"}

def test_webhook_verification_fail_token(client, test_webhook_token):
    response = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "challenge_code"
        }
    )
    assert response.status_code == 403

def test_webhook_receive_event(client, override_get_db):
    """
    Test receiving a new activity event.
    Since handling is mocked/placeholder for now, just check 200 OK
    """
    payload = {
        "object_type": "activity",
        "object_id": 12345,
        "aspect_type": "create",
        "owner_id": 999,
        "subscription_id": 1,
        "event_time": 1700000000,
        "updates": {}
    }
    response = client.post("/api/webhooks/strava", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    # Once we implement background tasks, we assert that the task was queued
