import time

import pytest

from app.models import StravaAccount, User
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    Tokens,
    ensure_valid_access_token,
)


def _make_account(db, *, expires_at: int) -> StravaAccount:
    user = User(email="auth_test@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=42,
        access_token="old_access",
        refresh_token="old_refresh",
        expires_at=expires_at,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return account


@pytest.mark.asyncio
async def test_ensure_valid_access_token_refreshes_when_expired(db):
    account = _make_account(db, expires_at=int(time.time()) - 3600)
    adapter = InMemoryStravaAdapter()
    new_tokens = Tokens(
        access_token="new_access",
        refresh_token="new_refresh",
        expires_at=int(time.time()) + 21600,
    )
    adapter.seed_refresh_response(new_tokens)

    token = await ensure_valid_access_token(db, account, adapter)

    assert token == "new_access"
    assert account.access_token == "new_access"
    assert account.refresh_token == "new_refresh"
    assert account.expires_at == new_tokens.expires_at
    assert adapter.refresh_calls == ["old_refresh"]


@pytest.mark.asyncio
async def test_ensure_valid_access_token_returns_existing_when_valid(db):
    account = _make_account(db, expires_at=int(time.time()) + 3600)
    adapter = InMemoryStravaAdapter()

    token = await ensure_valid_access_token(db, account, adapter)

    assert token == "old_access"
    assert adapter.refresh_calls == []
