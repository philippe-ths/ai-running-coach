"""Clerk session verification and per-user identity resolution (ADR 0022).

The frontend (Vercel) owns the Clerk session; the backend verifies the session
JWT itself against Clerk's JWKS and derives the durable identity (the verified
email) from it, so it never blindly trusts an unsigned header. This is the
security split of ADR 0022: Clerk owns the auth-provider attack surface; we own
only token verification and tenant isolation.

Transport: the Vercel proxy and the server-side API client forward the Clerk
session token in the ``X-Clerk-Session-Token`` header. The HTTP Basic
credential stays in ``Authorization`` as the frontend-to-backend service secret
(BasicAuthMiddleware), so the two credentials never collide.

Enforcement: ``verify_clerk_session`` is a FastAPI dependency applied to every
application router (not health/auth/webhooks). When Clerk is configured it
requires a valid session and returns the resolved ``User``; when it is not
configured it degrades to the single local user outside production (local dev +
the test suite keep working) and fails closed in production.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models import StravaAccount, User

logger = logging.getLogger(__name__)

SESSION_TOKEN_HEADER = "X-Clerk-Session-Token"

# Email may be carried directly on the session token if the Clerk session-token
# template adds it (the recommended, zero-extra-call setup). These are the keys
# we accept, in order.
_EMAIL_CLAIM_KEYS = ("email", "email_address", "primary_email_address")

# The placeholder-email domain the email-non-null migration backfills onto the
# pre-Phase-2 single user. Used to identify the legacy row for owner reconcile.
PLACEHOLDER_EMAIL_DOMAIN = "@placeholder.invalid"


class ClerkAuthError(Exception):
    """A session token could not be verified or an email could not be resolved."""


class ClerkVerifier:
    """Verifies a Clerk session JWT against a JWKS, RS256 only.

    The JWKS source is injectable (``jwk_client``) so tests exercise real RS256
    signature verification against a controlled keypair with no network. In
    production the default ``jwt.PyJWKClient`` fetches and caches Clerk's keys.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        issuer: str,
        authorized_parties: tuple[str, ...] = (),
        leeway: int = 0,
        jwk_client: Optional[object] = None,
    ) -> None:
        self._issuer = issuer
        self._authorized_parties = tuple(authorized_parties)
        self._leeway = leeway
        # PyJWKClient caches signing keys in-process after the first fetch.
        self._jwk_client = jwk_client or jwt.PyJWKClient(jwks_url)

    def verify(self, token: str) -> dict:
        """Return the verified claims, or raise ClerkAuthError.

        Pins the algorithm to RS256 (defeats the ``alg:none`` and HS256
        key-confusion attacks), requires exp/iat/sub, and validates the issuer.
        """
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            key = getattr(signing_key, "key", signing_key)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer if self._issuer else None,
                leeway=self._leeway,
                options={
                    "verify_aud": False,
                    "verify_iss": bool(self._issuer),
                    "require": ["exp", "iat", "sub"],
                },
            )
        except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
            raise ClerkAuthError(f"invalid session token: {exc}") from exc
        except Exception as exc:  # JWKS fetch/transport errors, malformed input
            raise ClerkAuthError(f"could not verify session token: {exc}") from exc

        if self._authorized_parties:
            azp = claims.get("azp")
            if azp not in self._authorized_parties:
                raise ClerkAuthError("token authorized party not allowed")

        return claims


# --- verifier singleton (lazy, swappable in tests) -------------------------

_verifier_lock = threading.Lock()
_verifier: Optional[ClerkVerifier] = None
_verifier_jwks_url: Optional[str] = None


def get_verifier() -> ClerkVerifier:
    """Return the process verifier, rebuilding it if the JWKS URL changed.

    Tests monkeypatch this function (or call ``reset_verifier_cache``) to inject
    a controlled-keypair verifier.
    """
    global _verifier, _verifier_jwks_url
    with _verifier_lock:
        if _verifier is None or _verifier_jwks_url != settings.CLERK_JWKS_URL:
            _verifier = ClerkVerifier(
                settings.CLERK_JWKS_URL,
                issuer=settings.clerk_issuer,
                authorized_parties=tuple(settings.clerk_authorized_parties_list),
                leeway=settings.CLERK_LEEWAY_SECONDS,
            )
            _verifier_jwks_url = settings.CLERK_JWKS_URL
        return _verifier


def reset_verifier_cache() -> None:
    global _verifier, _verifier_jwks_url
    with _verifier_lock:
        _verifier = None
        _verifier_jwks_url = None


# --- email resolution ------------------------------------------------------

def _email_from_claims(claims: dict) -> Optional[str]:
    for key in _EMAIL_CLAIM_KEYS:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def fetch_email_from_clerk_api(clerk_user_id: str) -> Optional[str]:
    """Fall back to the Clerk Backend API to read a user's primary email.

    Only used when the session token carries no email claim. Configuring the
    Clerk session-token template to include ``email`` avoids this call. Returns
    None on any failure (the caller turns that into a 401).
    """
    if not settings.CLERK_SECRET_KEY:
        return None
    import httpx

    try:
        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # network/transport/parse
        logger.warning("clerk_email_fetch_failed", extra={"error": str(exc)})
        return None

    primary_id = data.get("primary_email_address_id")
    addresses = data.get("email_addresses") or []
    for addr in addresses:
        if addr.get("id") == primary_id:
            email = addr.get("email_address")
            if isinstance(email, str) and email.strip():
                return email.strip()
    # fall back to the first verified address
    for addr in addresses:
        email = addr.get("email_address")
        if isinstance(email, str) and email.strip():
            return email.strip()
    return None


def resolve_email(claims: dict) -> str:
    """The verified email for a set of claims, or raise ClerkAuthError."""
    email = _email_from_claims(claims)
    if email:
        return email
    sub = claims.get("sub")
    if sub:
        email = fetch_email_from_clerk_api(sub)
    if not email:
        raise ClerkAuthError("no verified email on session")
    return email


# --- user resolution / owner reconcile -------------------------------------

def _find_legacy_user(db: Session) -> Optional[User]:
    """The pre-Phase-2 single user, for owner reconcile.

    Prefer the Strava-linked user (the real data owner); otherwise any user
    still carrying the migration placeholder email. Returns None if no such row
    exists (so we never adopt an already-real account).
    """
    placeholder_user = (
        db.execute(
            select(User).where(User.email.like(f"%{PLACEHOLDER_EMAIL_DOMAIN}"))
        )
        .scalars()
        .first()
    )
    if placeholder_user is None:
        return None
    strava = db.execute(select(StravaAccount)).scalars().first()
    if strava is not None and str(strava.user.email or "").endswith(PLACEHOLDER_EMAIL_DOMAIN):
        return strava.user
    return placeholder_user


def resolve_user_by_email(db: Session, email: str) -> User:
    """Get-or-create the user for a verified email, with owner reconcile.

    On the owner's first sign-in the legacy placeholder user is adopted (its
    email set to the owner address) so the owner's existing data is not stranded
    behind a fresh empty account.
    """
    normalized = email.strip().lower()
    user = _lookup_user_by_email(db, normalized)
    if user is not None:
        return user

    owner = settings.OWNER_EMAIL.strip().lower()
    if owner and normalized == owner:
        legacy = _find_legacy_user(db)
        if legacy is not None:
            legacy.email = normalized
            db.commit()
            db.refresh(legacy)
            logger.info("owner_user_reconciled", extra={"user_id": str(legacy.id)})
            return legacy

    # Race-safe create. A new user's first authenticated page load fires several
    # API calls concurrently; each one lands here having missed the lookup above,
    # so they all attempt the INSERT. The first wins; the rest violate the unique
    # email constraint and would otherwise 500 on the user's very first screen.
    # Recover by rolling back and reusing the row the winner created. This also
    # covers the owner-reconcile race above: a competing request that already
    # adopted the legacy row makes _find_legacy_user miss here, so we fall through
    # and recover the just-reconciled owner the same way.
    user = User(email=normalized)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Lost the unique-email race. Roll back our failed INSERT and reuse the
        # row the winner committed. Expunge the failed instance so a later
        # autoflush cannot retry the conflicting INSERT.
        db.rollback()
        if user in db:
            db.expunge(user)
        existing = _lookup_user_by_email(db, normalized)
        if existing is None:
            raise
        return existing
    db.refresh(user)
    return user


def _lookup_user_by_email(db: Session, normalized: str) -> Optional[User]:
    """Case-insensitive lookup of a user by an already-normalized email."""
    return (
        db.execute(select(User).where(func.lower(User.email) == normalized))
        .scalars()
        .first()
    )


def _degraded_single_user(db: Session) -> Optional[User]:
    """The one local user when Clerk is unconfigured outside production.

    Prefer the Strava-linked user (matches the legacy profile resolution), else
    any user. Does not create a row -- callers that need a default own that.
    """
    strava = db.execute(select(StravaAccount)).scalars().first()
    if strava is not None:
        return strava.user
    return db.execute(select(User)).scalars().first()


# --- the FastAPI dependency ------------------------------------------------

def verify_clerk_session(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolve the authenticated user for the request.

    Clerk configured: verify the ``X-Clerk-Session-Token`` JWT and resolve the
    user by verified email (401 on any failure). Clerk unconfigured: degrade to
    the single local user outside production; fail closed (503) in production.
    """
    if not settings.clerk_enabled:
        if settings.APP_ENV == "production":
            logger.critical("clerk_not_configured_in_production")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service Unavailable: auth misconfigured",
            )
        return _degraded_single_user(db)

    token = request.headers.get(SESSION_TOKEN_HEADER)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session token",
        )

    try:
        claims = get_verifier().verify(token)
        email = resolve_email(claims)
    except ClerkAuthError as exc:
        logger.info("session_rejected", extra={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
        ) from exc

    return resolve_user_by_email(db, email)
