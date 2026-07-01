import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.core.oauth_state import decode_state, encode_state
from app.main import app
from app.models import StravaAccount, User
from app.services.strava_ingestion import (
    InMemoryStravaAdapter,
    Tokens,
    ensure_valid_access_token,
    set_strava_port,
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


@pytest.mark.asyncio
async def test_refresh_defers_to_a_concurrent_winner_and_reuses_its_token(db):
    """#597: if another caller rotated the token while we waited for the
    per-account lock, reuse that token rather than refreshing again with the now
    stale refresh token (which Strava would reject / could unlink the account)."""
    from app.services.strava_ingestion import ingestion

    account = _make_account(db, expires_at=int(time.time()) - 3600)
    adapter = InMemoryStravaAdapter()
    adapter.seed_refresh_response(
        Tokens(access_token="ours", refresh_token="ours_r", expires_at=int(time.time()) + 21600)
    )

    class _WinnerLock:
        """Acquiring models the winner having finished a refresh while we waited."""

        def acquire(self):
            db.query(StravaAccount).filter(StravaAccount.id == account.id).update(
                {
                    "access_token": "winner_access",
                    "refresh_token": "winner_refresh",
                    "expires_at": int(time.time()) + 21600,
                }
            )
            db.commit()
            return True

        def release(self):
            pass

    fake_redis = MagicMock()
    fake_redis.lock.return_value = _WinnerLock()
    with patch.object(ingestion, "redis_conn", fake_redis):
        token = await ensure_valid_access_token(db, account, adapter)

    assert token == "winner_access"
    assert adapter.refresh_calls == []  # we did NOT burn the stale refresh token


@pytest.mark.asyncio
async def test_refresh_degrades_to_unlocked_when_redis_unavailable(db):
    """A coordination outage must never block ingestion: with Redis down the
    refresh still proceeds (unlocked, best effort)."""
    from app.services.strava_ingestion import ingestion

    account = _make_account(db, expires_at=int(time.time()) - 3600)
    adapter = InMemoryStravaAdapter()
    adapter.seed_refresh_response(
        Tokens(access_token="new_access", refresh_token="new_refresh", expires_at=int(time.time()) + 21600)
    )

    fake_redis = MagicMock()
    fake_redis.lock.side_effect = RuntimeError("redis down")
    with patch.object(ingestion, "redis_conn", fake_redis):
        token = await ensure_valid_access_token(db, account, adapter)

    assert token == "new_access"
    assert adapter.refresh_calls == ["old_refresh"]


@pytest.mark.asyncio
async def test_refresh_takes_and_releases_a_per_account_lock(db):
    """The refresh path acquires and releases the per-account lock, keyed so two
    accounts never contend (#597)."""
    from app.services.strava_ingestion import ingestion

    account = _make_account(db, expires_at=int(time.time()) - 3600)
    adapter = InMemoryStravaAdapter()
    adapter.seed_refresh_response(
        Tokens(access_token="new_access", refresh_token="new_refresh", expires_at=int(time.time()) + 21600)
    )

    lock = MagicMock()
    lock.acquire.return_value = True
    fake_redis = MagicMock()
    fake_redis.lock.return_value = lock
    with patch.object(ingestion, "redis_conn", fake_redis):
        token = await ensure_valid_access_token(db, account, adapter)

    assert token == "new_access"
    assert adapter.refresh_calls == ["old_refresh"]
    lock.acquire.assert_called_once()
    lock.release.assert_called_once()
    assert str(account.id) in fake_redis.lock.call_args.args[0]


def test_strava_status_returns_disconnected_when_no_account(client: TestClient):
    response = client.get("/api/auth/strava/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "athlete_id": None,
        "scope": None,
        "expires_at": None,
    }


def test_strava_status_returns_connected_when_account_linked(client: TestClient, db):
    expires_at = int(time.time()) + 3600
    _make_account(db, expires_at=expires_at)

    response = client.get("/api/auth/strava/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "athlete_id": 42,
        "scope": "read,activity:read_all",
        "expires_at": expires_at,
    }


def test_strava_status_is_scoped_to_the_signed_in_user(
    client: TestClient, db, _as_user
):
    # Two runners each have their own Strava account. The status read must
    # surface the signed-in user's OWN connection, never the first row globally
    # (#532): no cross-tenant athlete id / scope leak.
    runner_a = _new_user(db, "runner_a@example.com")
    runner_b = _new_user(db, "runner_b@example.com")
    db.add(
        StravaAccount(
            user_id=runner_a.id,
            strava_athlete_id=111,
            access_token="a",
            refresh_token="a",
            expires_at=int(time.time()) + 3600,
            scope="read,activity:read_all",
        )
    )
    db.add(
        StravaAccount(
            user_id=runner_b.id,
            strava_athlete_id=222,
            access_token="b",
            refresh_token="b",
            expires_at=int(time.time()) + 3600,
            scope="read",
        )
    )
    db.commit()

    _as_user(runner_b)
    response = client.get("/api/auth/strava/status")

    assert response.status_code == 200
    assert response.json()["athlete_id"] == 222
    assert response.json()["scope"] == "read"


def test_strava_status_disconnected_for_user_without_account(
    client: TestClient, db, _as_user
):
    # A signed-in runner with no linked account reads "not connected" even
    # though another runner's account exists in the table (#532).
    other = _new_user(db, "linked_other@example.com")
    db.add(
        StravaAccount(
            user_id=other.id,
            strava_athlete_id=333,
            access_token="x",
            refresh_token="x",
            expires_at=int(time.time()) + 3600,
            scope="read",
        )
    )
    db.commit()
    no_account_user = _new_user(db, "no_account@example.com")

    _as_user(no_account_user)
    response = client.get("/api/auth/strava/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "athlete_id": None,
        "scope": None,
        "expires_at": None,
    }


# --- #469: signed-state multi-user OAuth linking ---------------------------


@pytest.fixture
def _as_user(db):
    """Inject a specific authenticated user into the session-gated routes."""

    def _install(user: User):
        app.dependency_overrides[verify_clerk_session] = lambda: user

    yield _install
    app.dependency_overrides.pop(verify_clerk_session, None)


@pytest.fixture
def _inmemory_strava():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


def _new_user(db, email: str) -> User:
    user = User(email=email)
    db.add(user)
    db.commit()
    return user


def test_login_requires_session_when_clerk_enabled(client: TestClient, monkeypatch):
    # Clerk on + no session token -> the login route is now gated (401).
    monkeypatch.setattr(settings, "CLERK_JWKS_URL", "https://clerk.test/.well-known/jwks.json")

    response = client.get("/api/auth/strava/login", follow_redirects=False)

    assert response.status_code == 401


def test_login_mints_state_for_the_signed_in_user(client: TestClient, db, _as_user):
    user = _new_user(db, "login_state@example.com")
    _as_user(user)

    response = client.get("/api/auth/strava/login", follow_redirects=False)

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    assert decode_state(state) == user.id


def test_callback_links_new_account_to_state_user(
    client: TestClient, db, _inmemory_strava
):
    # Two users exist, so the legacy single-owner heuristic cannot pick one;
    # the signed state must steer the link to the right user.
    _new_user(db, "other@example.com")
    target = _new_user(db, "target@example.com")
    _inmemory_strava.seed_exchange_response(
        Tokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=int(time.time()) + 3600,
            athlete={"id": 9001},
        )
    )

    response = client.get(
        "/api/auth/strava/callback",
        params={"code": "x", "state": encode_state(target.id)},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    account = (
        db.query(StravaAccount).filter(StravaAccount.strava_athlete_id == 9001).one()
    )
    assert account.user_id == target.id


def test_callback_without_state_preserves_single_owner(
    client: TestClient, db, _inmemory_strava
):
    owner = _new_user(db, "solo@example.com")
    _inmemory_strava.seed_exchange_response(
        Tokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=int(time.time()) + 3600,
            athlete={"id": 9002},
        )
    )

    response = client.get(
        "/api/auth/strava/callback",
        params={"code": "x"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    account = (
        db.query(StravaAccount).filter(StravaAccount.strava_athlete_id == 9002).one()
    )
    assert account.user_id == owner.id


def test_callback_with_invalid_state_falls_back_to_single_owner(
    client: TestClient, db, _inmemory_strava
):
    # A tampered/expired state must not crash; it falls through to the legacy
    # single-owner rule rather than mis-linking.
    owner = _new_user(db, "fallback@example.com")
    _inmemory_strava.seed_exchange_response(
        Tokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=int(time.time()) + 3600,
            athlete={"id": 9003},
        )
    )

    response = client.get(
        "/api/auth/strava/callback",
        params={"code": "x", "state": "garbage.notavalidtoken"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    account = (
        db.query(StravaAccount).filter(StravaAccount.strava_athlete_id == 9003).one()
    )
    assert account.user_id == owner.id


def test_callback_in_production_rejects_new_athlete_without_valid_state(
    client: TestClient, db, _inmemory_strava, monkeypatch
):
    """#599: in production an absent/invalid state on a NEW athlete is rejected
    (reconnect), never linked to a guessed owner or an orphan placeholder — even
    with a single existing user (the old single-owner fallback is prod-disabled)."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    owner = _new_user(db, "prod_owner@example.com")
    users_before = db.query(User).count()
    _inmemory_strava.seed_exchange_response(
        Tokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=int(time.time()) + 3600,
            athlete={"id": 9101},
        )
    )

    response = client.get(
        "/api/auth/strava/callback",
        params={"code": "x"},  # no state
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "strava_error=state_expired" in response.headers["location"]
    # No account linked and no orphan placeholder user minted.
    assert (
        db.query(StravaAccount).filter(StravaAccount.strava_athlete_id == 9101).first()
        is None
    )
    assert db.query(User).count() == users_before
    assert owner.email == "prod_owner@example.com"


def test_callback_in_production_links_new_athlete_with_valid_state(
    client: TestClient, db, _inmemory_strava, monkeypatch
):
    """A valid signed state still links correctly in production (#599 rejects only
    the absent/invalid-state path)."""
    monkeypatch.setattr(settings, "APP_ENV", "production")
    _new_user(db, "other_prod@example.com")
    target = _new_user(db, "target_prod@example.com")
    _inmemory_strava.seed_exchange_response(
        Tokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=int(time.time()) + 3600,
            athlete={"id": 9102},
        )
    )

    response = client.get(
        "/api/auth/strava/callback",
        params={"code": "x", "state": encode_state(target.id)},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    account = (
        db.query(StravaAccount).filter(StravaAccount.strava_athlete_id == 9102).one()
    )
    assert account.user_id == target.id
