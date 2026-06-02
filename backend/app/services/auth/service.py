"""Magic-link token and server-side session logic (ADR 0005, #118).

Raw tokens (the link token and the session cookie value) are high-entropy
secrets that are never stored; only their SHA-256 hash is persisted, so a
database leak yields no usable credential. The db-injectable functions carry
the real logic and are unit-tested directly; `resolve_session_user_id_from_token`
is the thin wrapper the request middleware uses, opening its own session because
it runs outside the per-request `get_db` dependency.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.models import LoginToken, Session, User


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(when: datetime) -> bool:
    # Stored timezone-aware, but SQLite (tests) round-trips naive datetimes;
    # treat a naive value as UTC so the comparison never raises.
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return _now() >= when


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_login_token(db: DBSession, email: str) -> str:
    """Create a single-use login token for ``email``; return the raw token.

    The raw token is what gets emailed; only its hash is stored.
    """
    raw = secrets.token_urlsafe(32)
    db.add(
        LoginToken(
            token_hash=_hash(raw),
            email=normalize_email(email),
            expires_at=_now() + timedelta(seconds=settings.LOGIN_TOKEN_TTL_SECONDS),
        )
    )
    db.commit()
    return raw


def consume_login_token(db: DBSession, raw: str) -> Optional[User]:
    """Validate and spend a login token, returning the resolved ``User``.

    Returns ``None`` when the token is unknown, already used, or expired. The
    first valid use for an email creates the ``User``; later uses match by
    email, so an expired cookie just means signing in again restores the same
    account.
    """
    if not raw:
        return None
    record = (
        db.execute(select(LoginToken).where(LoginToken.token_hash == _hash(raw)))
        .scalars()
        .first()
    )
    if record is None or record.used_at is not None or _is_expired(record.expires_at):
        return None

    record.used_at = _now()
    user = (
        db.execute(select(User).where(User.email == record.email)).scalars().first()
    )
    if user is None:
        user = User(email=record.email)
        db.add(user)
        db.flush()
    db.commit()
    return user


def create_session(db: DBSession, user: User) -> str:
    """Create a session for ``user``; return the raw cookie token."""
    raw = secrets.token_urlsafe(32)
    db.add(
        Session(
            user_id=user.id,
            token_hash=_hash(raw),
            expires_at=_now() + timedelta(seconds=settings.SESSION_TTL_SECONDS),
        )
    )
    db.commit()
    return raw


def resolve_session_user_id(db: DBSession, raw: str) -> Optional[uuid.UUID]:
    """Return the user id for a valid session token, else ``None``."""
    if not raw:
        return None
    session = (
        db.execute(select(Session).where(Session.token_hash == _hash(raw)))
        .scalars()
        .first()
    )
    if session is None or _is_expired(session.expires_at):
        return None
    return session.user_id


def delete_session(db: DBSession, raw: str) -> None:
    """Revoke a session by deleting its row (logout)."""
    if not raw:
        return
    session = (
        db.execute(select(Session).where(Session.token_hash == _hash(raw)))
        .scalars()
        .first()
    )
    if session is not None:
        db.delete(session)
        db.commit()


def resolve_session_user_id_from_token(raw: str) -> Optional[uuid.UUID]:
    """Middleware entry point: resolve a session token using a fresh DB session.

    Runs outside the per-request ``get_db`` dependency, so it owns its session.
    """
    if not raw:
        return None
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return resolve_session_user_id(db, raw)
    finally:
        db.close()
