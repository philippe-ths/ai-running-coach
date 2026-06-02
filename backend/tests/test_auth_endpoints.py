"""request-link / verify / logout endpoints (ADR 0005)."""

import pytest
from sqlalchemy import select

import app.api.auth as auth_api
from app.core.config import settings
from app.models import LoginToken, Session, User
from app.services.auth import create_login_token, create_session
from app.services.notifications import InMemoryNotifier, set_notifier


class _AllowLimiter:
    def allow_login_request(self, **kwargs):
        return True


class _DenyLimiter:
    def allow_login_request(self, **kwargs):
        return False


@pytest.fixture
def notifier():
    inbox = InMemoryNotifier()
    set_notifier(inbox)
    yield inbox
    set_notifier(None)


def test_request_link_sends_email_with_token(client, db, notifier, monkeypatch):
    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: _AllowLimiter())

    res = client.post("/api/auth/request-link", json={"email": "Runner@Example.com"})

    assert res.status_code == 202
    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.to == "runner@example.com"
    assert "/api/auth/verify?token=" in sent.text
    # A token row was persisted (hashed).
    assert db.execute(select(LoginToken)).scalars().first() is not None


def test_request_link_rejects_malformed_email(client, monkeypatch):
    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: _AllowLimiter())
    res = client.post("/api/auth/request-link", json={"email": "not-an-email"})
    assert res.status_code == 422


def test_request_link_is_rate_limited(client, notifier, monkeypatch):
    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: _DenyLimiter())
    res = client.post("/api/auth/request-link", json={"email": "spam@example.com"})
    assert res.status_code == 429
    assert notifier.sent == []


def test_verify_valid_token_sets_session_cookie(client, db):
    raw = create_login_token(db, "verify@example.com")

    res = client.get(f"/api/auth/verify?token={raw}", follow_redirects=False)

    assert res.status_code == 303
    assert res.headers["location"] == settings.APP_BASE_URL
    assert settings.SESSION_COOKIE_NAME in res.headers.get("set-cookie", "")
    # User + session now exist.
    user = db.execute(select(User).where(User.email == "verify@example.com")).scalars().first()
    assert user is not None
    assert db.execute(select(Session).where(Session.user_id == user.id)).scalars().first() is not None


def test_verify_invalid_token_redirects_to_login_error(client, db):
    res = client.get("/api/auth/verify?token=bogus", follow_redirects=False)
    assert res.status_code == 303
    assert "error=invalid_link" in res.headers["location"]
    assert settings.SESSION_COOKIE_NAME not in res.headers.get("set-cookie", "")


def test_logout_revokes_session(client, db):
    user = User(email="bye@example.com")
    db.add(user)
    db.commit()
    raw = create_session(db, user)

    res = client.post("/api/auth/logout", cookies={settings.SESSION_COOKIE_NAME: raw})

    assert res.status_code == 204
    assert db.execute(select(Session)).scalars().first() is None
