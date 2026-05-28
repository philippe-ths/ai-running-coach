"""Tests for the Phase 1 HTTP basic-auth middleware.

Verifies the exempt list keeps webhooks and the health endpoint publicly
reachable, that gated routes return 401 without correct credentials, and that
the middleware is a no-op when credentials are unset (local-dev convenience).
"""

import base64

import pytest

from app.core.config import settings


_AUTH_USER = "philippe"
_AUTH_PASSWORD = "test-only-do-not-deploy"


def _basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", _AUTH_USER)
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", _AUTH_PASSWORD)


@pytest.fixture
def auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "")


class TestExemptRoutes:
    def test_health_endpoint_is_reachable_without_credentials(self, client, auth_enabled):
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_webhook_endpoint_is_reachable_without_credentials(self, client, auth_enabled):
        response = client.get("/api/webhooks/strava")
        assert response.status_code != 401


class TestGatedRoutes:
    def test_gated_route_rejects_request_without_authorization_header(self, client, auth_enabled):
        response = client.get("/api/profile")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate", "").lower().startswith("basic")

    def test_gated_route_accepts_correct_credentials(self, client, auth_enabled):
        response = client.get(
            "/api/profile",
            headers={"Authorization": _basic(_AUTH_USER, _AUTH_PASSWORD)},
        )
        assert response.status_code == 200

    def test_gated_route_rejects_wrong_password(self, client, auth_enabled):
        response = client.get(
            "/api/profile",
            headers={"Authorization": _basic(_AUTH_USER, "wrong-password")},
        )
        assert response.status_code == 401

    def test_gated_route_rejects_wrong_user(self, client, auth_enabled):
        response = client.get(
            "/api/profile",
            headers={"Authorization": _basic("intruder", _AUTH_PASSWORD)},
        )
        assert response.status_code == 401

    def test_gated_route_rejects_non_basic_scheme(self, client, auth_enabled):
        response = client.get(
            "/api/profile",
            headers={"Authorization": "Bearer some-token"},
        )
        assert response.status_code == 401

    def test_gated_route_rejects_malformed_base64(self, client, auth_enabled):
        response = client.get(
            "/api/profile",
            headers={"Authorization": "Basic not-valid-base64!!!"},
        )
        assert response.status_code == 401

    def test_gated_route_rejects_credentials_without_colon(self, client, auth_enabled):
        token = base64.b64encode(b"no-colon-here").decode("ascii")
        response = client.get(
            "/api/profile",
            headers={"Authorization": f"Basic {token}"},
        )
        assert response.status_code == 401


class TestDisabledByDefault:
    def test_gated_route_is_open_when_credentials_unset(self, client, auth_disabled):
        response = client.get("/api/profile")
        assert response.status_code == 200

    def test_gated_route_is_open_when_only_user_set(self, client, monkeypatch):
        monkeypatch.setattr(settings, "BASIC_AUTH_USER", _AUTH_USER)
        monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "")
        response = client.get("/api/profile")
        assert response.status_code == 200
