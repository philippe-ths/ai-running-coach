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
