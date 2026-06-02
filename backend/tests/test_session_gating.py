"""SessionAuthMiddleware gating (ADR 0005, #118 PR-A).

Migrate-aspect oracle: the exempt set stays {/api/health, /api/webhooks,
/api/auth}; gated routes require a valid session OR (transitionally) basic auth.
The session DB lookup is monkeypatched here so gating is tested without a DB;
the lookup logic itself is covered by test_auth_service.
"""

import uuid

import pytest

import app.core.session_auth as session_auth
from app.core.config import settings

GATED = "/api/profile"


@pytest.fixture
def valid_session(monkeypatch):
    """Make exactly cookie value 'good-token' resolve to a user."""
    uid = uuid.uuid4()

    def fake_resolve(raw):
        return uid if raw == "good-token" else None

    monkeypatch.setattr(session_auth, "resolve_session_user_id_from_token", fake_resolve)
    return uid


def test_valid_session_grants_access(client, valid_session):
    res = client.get(GATED, cookies={settings.SESSION_COOKIE_NAME: "good-token"})
    assert res.status_code == 200


def test_no_credentials_allowed_when_basic_auth_unset(client, valid_session, monkeypatch):
    # Test env leaves basic-auth creds empty -> middleware is a no-op gate.
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "")
    res = client.get(GATED)
    assert res.status_code == 200


def test_invalid_session_falls_back_to_basic_auth(client, valid_session, monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "u")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "p")

    # Bad cookie, no Authorization header -> basic-auth fallback rejects.
    res = client.get(GATED, cookies={settings.SESSION_COOKIE_NAME: "wrong"})
    assert res.status_code == 401

    # Valid session bypasses basic auth entirely.
    res2 = client.get(GATED, cookies={settings.SESSION_COOKIE_NAME: "good-token"})
    assert res2.status_code == 200

    # Correct basic-auth header also passes when no session is present.
    res3 = client.get(GATED, auth=("u", "p"))
    assert res3.status_code == 200


def test_health_is_exempt(client, monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "u")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "p")
    res = client.get("/api/health")
    assert res.status_code == 200


def test_auth_endpoints_are_exempt(client, monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "u")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "p")
    # No session, no basic-auth header, but /api/auth/* is exempt -> not a 401.
    res = client.get("/api/auth/verify?token=bogus", follow_redirects=False)
    assert res.status_code != 401
