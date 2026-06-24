"""Phase 2 Clerk session verification + identity resolution (ADR 0022, #118).

Security-first coverage (aiw-security-testing): the negative paths are the point.
Real RS256 signature verification runs against a controlled, in-test RSA keypair
with no network -- the JWKS fetch is the only boundary stubbed -- so the
algorithm-confusion, expiry, wrong-signature, and wrong-issuer denials are
exercised against real crypto, not a mock of the verifier.

The live Clerk dev instance is the runtime oracle for the end-to-end sign-in
(exercised locally, out of band); these tests pin the verification logic and the
dependency's enforcement/degrade contract.
"""

import asyncio
import datetime as dt

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import clerk_auth
from app.core.clerk_auth import (
    ClerkAuthError,
    ClerkVerifier,
    resolve_user_by_email,
)
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import StravaAccount, User

TEST_ISSUER = "https://test-instance.clerk.accounts.dev"
TEST_JWKS_URL = f"{TEST_ISSUER}/.well-known/jwks.json"
HEADER = clerk_auth.SESSION_TOKEN_HEADER

# Gated-route discovery: every /api route NOT under one of these prefixes must
# carry the verify_clerk_session dependency. Mirrors app/main.py.
_EXEMPT_PREFIXES = ("/api/health", "/api/auth", "/api/webhooks")


# --- keypair + token helpers ----------------------------------------------

@pytest.fixture(scope="module")
def rsa_keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    """Stubs only the JWKS network fetch; the returned key drives real RS256."""

    def __init__(self, public_key):
        self._key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._key)


def _make_token(priv, *, claims=None, alg="RS256", key=None, headers=None):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": "user_clerk_abc",
        "email": "runner@example.com",
        "iss": TEST_ISSUER,
        "iat": now,
        "exp": now + dt.timedelta(hours=1),
    }
    if claims is not None:
        payload.update(claims)
    signing_key = key if key is not None else priv
    return jwt.encode(payload, signing_key, algorithm=alg, headers=headers)


def _verifier(pub):
    return ClerkVerifier(
        TEST_JWKS_URL, issuer=TEST_ISSUER, jwk_client=_FakeJWKClient(pub), leeway=30
    )


@pytest.fixture
def clerk_env(monkeypatch, rsa_keys):
    """Turn Clerk auth ON with the in-test verifier injected."""
    priv, pub = rsa_keys
    monkeypatch.setattr(settings, "CLERK_JWKS_URL", TEST_JWKS_URL)
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "sk_test-do-not-use-in-prod")
    monkeypatch.setattr(clerk_auth, "get_verifier", lambda: _verifier(pub))
    return priv, pub


@pytest.fixture
def app_with_db(db):
    """Bind the app's get_db to the test session for ASGITransport tests."""
    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    yield app
    app.dependency_overrides.clear()


# --- verifier crypto matrix (unit, real RS256) -----------------------------

class TestVerifierCrypto:
    def test_valid_token_returns_claims(self, rsa_keys):
        priv, pub = rsa_keys
        claims = _verifier(pub).verify(_make_token(priv))
        assert claims["email"] == "runner@example.com"
        assert claims["sub"] == "user_clerk_abc"

    def test_expired_token_rejected(self, rsa_keys):
        priv, pub = rsa_keys
        now = dt.datetime.now(dt.timezone.utc)
        token = _make_token(
            priv,
            claims={"iat": now - dt.timedelta(hours=2), "exp": now - dt.timedelta(hours=1)},
        )
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_wrong_signature_rejected(self, rsa_keys):
        priv, pub = rsa_keys
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _make_token(priv, key=attacker)  # signed by a key we don't trust
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_alg_none_rejected(self, rsa_keys):
        """The classic unsigned-token attack: alg=none must never verify."""
        _, pub = rsa_keys
        now = dt.datetime.now(dt.timezone.utc)
        payload = {
            "sub": "user_clerk_abc",
            "email": "attacker@example.com",
            "iss": TEST_ISSUER,
            "iat": now,
            "exp": now + dt.timedelta(hours=1),
        }
        token = jwt.encode(payload, key="", algorithm="none")
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_hs256_token_rejected(self, rsa_keys):
        """Any HS256 token is rejected (RS256-pinned), defeating key confusion.

        The key-confusion attack relies on the verifier accepting HS256 and the
        attacker signing with the RSA public key as the HMAC secret. Pinning the
        algorithm to RS256 rejects every HS256 token regardless of its secret,
        which is the defense; PyJWT additionally refuses to *encode* HS256 with a
        PEM key, so we sign with a plain secret to construct the rejected token.
        """
        _, pub = rsa_keys
        token = _make_token(None, key="attacker-hmac-secret-padding-0123456789", alg="HS256")
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_wrong_issuer_rejected(self, rsa_keys):
        priv, pub = rsa_keys
        token = _make_token(priv, claims={"iss": "https://evil.example.com"})
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_missing_required_claim_rejected(self, rsa_keys):
        priv, pub = rsa_keys
        now = dt.datetime.now(dt.timezone.utc)
        # No exp -> required claim missing.
        payload = {"sub": "x", "iss": TEST_ISSUER, "iat": now}
        token = jwt.encode(payload, priv, algorithm="RS256")
        with pytest.raises(ClerkAuthError):
            _verifier(pub).verify(token)

    def test_authorized_party_enforced(self, rsa_keys):
        priv, pub = rsa_keys
        verifier = ClerkVerifier(
            TEST_JWKS_URL,
            issuer=TEST_ISSUER,
            authorized_parties=("https://app.example.com",),
            jwk_client=_FakeJWKClient(pub),
            leeway=30,
        )
        bad = _make_token(priv, claims={"azp": "https://evil.example.com"})
        with pytest.raises(ClerkAuthError):
            verifier.verify(bad)
        ok = _make_token(priv, claims={"azp": "https://app.example.com"})
        assert verifier.verify(ok)["azp"] == "https://app.example.com"


# --- dependency enforcement over HTTP (TestClient) -------------------------

class TestSessionEnforcement:
    def test_valid_token_resolves_and_creates_user(self, client, clerk_env, db):
        priv, _ = clerk_env
        resp = client.get("/api/profile", headers={HEADER: _make_token(priv)})
        assert resp.status_code == 200
        user = db.execute(
            User.__table__.select().where(User.email == "runner@example.com")
        ).first()
        assert user is not None

    def test_missing_token_denied(self, client, clerk_env):
        assert client.get("/api/profile").status_code == 401

    def test_malformed_token_denied(self, client, clerk_env):
        resp = client.get("/api/profile", headers={HEADER: "not-a-jwt"})
        assert resp.status_code == 401

    def test_expired_token_denied_over_http(self, client, clerk_env):
        priv, _ = clerk_env
        now = dt.datetime.now(dt.timezone.utc)
        token = _make_token(
            priv,
            claims={"iat": now - dt.timedelta(hours=2), "exp": now - dt.timedelta(hours=1)},
        )
        assert client.get("/api/profile", headers={HEADER: token}).status_code == 401

    def test_health_exempt_without_token(self, client, clerk_env):
        assert client.get("/api/health").status_code == 200

    def test_strava_status_exempt_without_token(self, client, clerk_env):
        # Auth router is the OAuth handshake surface, exempt from per-user sessions.
        assert client.get("/api/auth/strava/status").status_code == 200


# --- email resolution (claim + Clerk API fallback) -------------------------

class TestEmailResolution:
    def test_email_api_fallback_used_when_no_claim(self, client, clerk_env, monkeypatch, db):
        priv, _ = clerk_env
        monkeypatch.setattr(
            clerk_auth, "fetch_email_from_clerk_api", lambda sub: "fallback@example.com"
        )
        token = _make_token(priv, claims={"email": None})
        # email=None means the claim is absent; resolve falls back to the API.
        resp = client.get("/api/profile", headers={HEADER: token})
        assert resp.status_code == 200
        assert db.execute(
            User.__table__.select().where(User.email == "fallback@example.com")
        ).first() is not None

    def test_no_email_anywhere_denied(self, client, clerk_env, monkeypatch):
        priv, _ = clerk_env
        monkeypatch.setattr(clerk_auth, "fetch_email_from_clerk_api", lambda sub: None)
        token = _make_token(priv, claims={"email": None})
        assert client.get("/api/profile", headers={HEADER: token}).status_code == 401


# --- identity resolution + owner reconcile (unit) --------------------------

class TestUserResolution:
    def test_returning_user_resolves_same_account(self, db):
        a = resolve_user_by_email(db, "Runner@Example.com")
        b = resolve_user_by_email(db, "runner@example.com")  # case-insensitive
        assert a.id == b.id
        assert db.query(User).count() == 1

    def test_new_email_creates_new_user(self, db):
        a = resolve_user_by_email(db, "one@example.com")
        b = resolve_user_by_email(db, "two@example.com")
        assert a.id != b.id

    def test_owner_reconcile_adopts_legacy_user(self, db, monkeypatch):
        monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@gmail.com")
        legacy = User(email="legacy-1234@placeholder.invalid")
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

        resolved = resolve_user_by_email(db, "owner@gmail.com")
        assert resolved.id == legacy_id  # adopted, not a fresh account
        assert resolved.email == "owner@gmail.com"
        assert db.query(User).count() == 1

    def test_owner_reconcile_prefers_strava_linked_legacy(self, db, monkeypatch):
        monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@gmail.com")
        plain_legacy = User(email="legacy-aaaa@placeholder.invalid")
        strava_legacy = User(email="legacy-bbbb@placeholder.invalid")
        db.add_all([plain_legacy, strava_legacy])
        db.flush()
        db.add(
            StravaAccount(
                user_id=strava_legacy.id,
                strava_athlete_id=99,
                access_token="t",
                refresh_token="r",
                expires_at=0,
                scope="read",
            )
        )
        db.commit()
        strava_legacy_id = strava_legacy.id

        resolved = resolve_user_by_email(db, "owner@gmail.com")
        assert resolved.id == strava_legacy_id

    def test_non_owner_does_not_adopt_legacy(self, db, monkeypatch):
        monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@gmail.com")
        legacy = User(email="legacy-1234@placeholder.invalid")
        db.add(legacy)
        db.commit()

        resolved = resolve_user_by_email(db, "stranger@example.com")
        assert resolved.email == "stranger@example.com"
        assert db.query(User).count() == 2  # legacy untouched


# --- structural fence: every app route is gated ----------------------------

def _flatten_calls(dependant):
    calls, stack = [], [dependant]
    while stack:
        d = stack.pop()
        if getattr(d, "call", None) is not None:
            calls.append(d.call)
        stack.extend(getattr(d, "dependencies", []))
    return calls


class TestGateCoverage:
    def test_every_application_route_requires_a_session(self):
        """No application /api route may be left ungated (fail-closed by review)."""
        ungated = []
        for route in app.routes:
            path = getattr(route, "path", "")
            dependant = getattr(route, "dependant", None)
            if dependant is None or not path.startswith("/api/"):
                continue
            if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
                continue
            if clerk_auth.verify_clerk_session not in _flatten_calls(dependant):
                ungated.append(f"{getattr(route, 'methods', '')} {path}")
        assert not ungated, f"ungated application routes: {ungated}"

    def test_exempt_routers_are_not_gated(self):
        for route in app.routes:
            path = getattr(route, "path", "")
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            if path.startswith("/api/health") or path.startswith("/api/webhooks"):
                assert clerk_auth.verify_clerk_session not in _flatten_calls(dependant)


# --- production fail-closed -------------------------------------------------

class TestProductionFailClosed:
    def test_unconfigured_clerk_in_production_returns_503(self, client, monkeypatch):
        # Pass the basic-auth service secret so the request clears the middleware
        # and actually reaches the Clerk dependency, isolating its 503 (an
        # unconfigured-Clerk-in-production fail-closed) from the basic layer's.
        import base64

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "CLERK_JWKS_URL", "")  # Clerk not configured
        monkeypatch.setattr(settings, "BASIC_AUTH_USER", "svc")
        monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "secret-test-only")
        token = base64.b64encode(b"svc:secret-test-only").decode("ascii")
        resp = client.get("/api/profile", headers={"Authorization": f"Basic {token}"})
        assert resp.status_code == 503


# --- real ASGI transport (httpx), not just TestClient ----------------------

class TestRealAsgiTransport:
    """Per the handoff: verify auth against a real ASGI transport, not only the
    in-process TestClient (which has masked real request behaviour before)."""

    def _get(self, headers):
        async def _call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                return await ac.get("/api/profile", headers=headers)
        return asyncio.run(_call())

    def test_valid_token_accepted(self, app_with_db, clerk_env):
        priv, _ = clerk_env
        resp = self._get({HEADER: _make_token(priv)})
        assert resp.status_code == 200

    def test_missing_token_rejected(self, app_with_db, clerk_env):
        assert self._get({}).status_code == 401
