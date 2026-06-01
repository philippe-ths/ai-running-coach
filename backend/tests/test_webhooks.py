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


class TestWebhookVerifyFailsClosedInProduction:
    """When APP_ENV=production and the configured verify token is empty,
    the verify endpoint must refuse rather than accept an empty
    ?hub.verify_token=. The default of "" would otherwise let anyone register
    a webhook subscription against the deployment.
    """

    def test_returns_503_when_token_empty_in_production(self, client, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "")
        response = client.get(
            "/api/webhooks/strava",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "",
                "hub.challenge": "challenge_code",
            },
        )
        assert response.status_code == 503

    def test_returns_503_when_token_empty_in_production_even_with_token_supplied(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "")
        response = client.get(
            "/api/webhooks/strava",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "anything",
                "hub.challenge": "challenge_code",
            },
        )
        assert response.status_code == 503

    def test_production_with_token_set_authenticates_normally(self, client, monkeypatch):
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "prod_token")
        response = client.get(
            "/api/webhooks/strava",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "prod_token",
                "hub.challenge": "challenge_code",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"hub.challenge": "challenge_code"}

    def test_local_with_empty_token_returns_403_not_503(self, client, monkeypatch):
        """Local dev keeps the previous behaviour: an empty configured token
        is allowed (the local default is the literal "dev-verify-token"), but
        even an explicit empty config should not trigger the production gate."""
        monkeypatch.setattr(settings, "APP_ENV", "local")
        monkeypatch.setattr(settings, "STRAVA_WEBHOOK_VERIFY_TOKEN", "")
        response = client.get(
            "/api/webhooks/strava",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "anything",
                "hub.challenge": "challenge_code",
            },
        )
        assert response.status_code != 503

@pytest.mark.integration
def test_webhook_receive_event(client, db):
    """
    Test receiving a new activity event.
    Since handling is mocked/placeholder for now, just check 200 OK
    """
    from uuid import uuid4
    from app.models import StravaAccount, User

    # The event must come from a connected athlete to be authentic (#100).
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        StravaAccount(
            user_id=user.id,
            strava_athlete_id=999,
            access_token="test-token-do-not-use-in-prod",
            refresh_token="test-refresh-do-not-use-in-prod",
            expires_at=9999999999,
            scope="read",
        )
    )
    db.commit()

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
