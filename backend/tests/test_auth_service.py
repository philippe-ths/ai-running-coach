"""Token and session logic (ADR 0005). Oracle: ADR 0005's specified semantics."""

import uuid

from sqlalchemy import select

from app.core.config import settings
from app.models import LoginToken, Session, User
from app.services.auth import (
    consume_login_token,
    create_login_token,
    create_session,
    delete_session,
    resolve_session_user_id,
)


def test_first_valid_token_use_creates_user(db):
    raw = create_login_token(db, "Runner@Example.com")

    user = consume_login_token(db, raw)

    assert user is not None
    assert user.email == "runner@example.com"  # normalized
    assert db.execute(select(User).where(User.email == "runner@example.com")).scalars().first() is not None


def test_returning_email_matches_same_user(db):
    existing = User(email="repeat@example.com")
    db.add(existing)
    db.commit()

    raw = create_login_token(db, "repeat@example.com")
    user = consume_login_token(db, raw)

    assert user is not None
    assert user.id == existing.id


def test_token_is_single_use(db):
    raw = create_login_token(db, "once@example.com")

    assert consume_login_token(db, raw) is not None
    assert consume_login_token(db, raw) is None


def test_expired_token_is_rejected(db, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_TOKEN_TTL_SECONDS", -1)
    raw = create_login_token(db, "expired@example.com")

    assert consume_login_token(db, raw) is None


def test_unknown_token_returns_none(db):
    assert consume_login_token(db, "not-a-real-token") is None


def test_raw_token_is_not_stored(db):
    raw = create_login_token(db, "secret@example.com")
    record = db.execute(select(LoginToken)).scalars().first()
    assert record.token_hash != raw
    assert raw not in record.token_hash


def test_session_round_trip(db):
    user = User(email="sess@example.com")
    db.add(user)
    db.commit()

    raw = create_session(db, user)
    assert resolve_session_user_id(db, raw) == user.id


def test_expired_session_returns_none(db, monkeypatch):
    user = User(email="oldsess@example.com")
    db.add(user)
    db.commit()
    monkeypatch.setattr(settings, "SESSION_TTL_SECONDS", -1)
    raw = create_session(db, user)

    assert resolve_session_user_id(db, raw) is None


def test_unknown_session_token_returns_none(db):
    assert resolve_session_user_id(db, "nope") is None


def test_delete_session_revokes_it(db):
    user = User(email="logout@example.com")
    db.add(user)
    db.commit()
    raw = create_session(db, user)
    assert resolve_session_user_id(db, raw) == user.id

    delete_session(db, raw)

    assert resolve_session_user_id(db, raw) is None
    assert db.execute(select(Session)).scalars().first() is None
