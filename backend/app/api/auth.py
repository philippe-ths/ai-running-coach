import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.core.config import settings
from app.db.session import get_db
from app.models import User, StravaAccount
from app.services.auth import (
    consume_login_token,
    create_login_token,
    create_session,
    delete_session,
    get_rate_limiter,
    normalize_email,
)
from app.services.notifications import Notification, get_notifier
from app.services.notifications.email_template import render_login_email
from app.services.strava_ingestion import get_strava_port

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginLinkRequest(BaseModel):
    email: str


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=settings.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )


@router.post("/auth/request-link", status_code=202)
def request_login_link(payload: LoginLinkRequest, request: Request, db: Session = Depends(get_db)):
    """Email a single-use magic-link to ``email``.

    Always returns 202 for a well-formed email so the response does not reveal
    whether an account exists. Rate-limited per email and per IP.
    """
    email = normalize_email(payload.email)
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Invalid email address")

    limiter = get_rate_limiter()
    if not limiter.allow_login_request(email=email, ip=_client_ip(request), now_epoch=time.time()):
        raise HTTPException(status_code=429, detail="Too many sign-in requests. Try again later.")

    raw = create_login_token(db, email)
    verify_url = f"{settings.APP_BASE_URL.rstrip('/')}/api/auth/verify?token={raw}"
    subject, html, text = render_login_email(
        verify_url=verify_url, ttl_minutes=settings.LOGIN_TOKEN_TTL_SECONDS // 60
    )
    try:
        get_notifier().send(Notification(to=email, subject=subject, html=html, text=text))
    except Exception:  # noqa: BLE001 - transport failures must not 500 the caller
        logger.exception("Failed to send login link to %s", email)
    return {"status": "sent"}


@router.get("/auth/verify")
def verify_login_link(token: str = Query(...), db: Session = Depends(get_db)):
    """Exchange a magic-link token for a session cookie, then redirect to the app."""
    user = consume_login_token(db, token)
    if user is None:
        return RedirectResponse(
            url=f"{settings.APP_BASE_URL.rstrip('/')}/login?error=invalid_link",
            status_code=303,
        )
    raw_session = create_session(db, user)
    response = RedirectResponse(url=settings.APP_BASE_URL, status_code=303)
    _set_session_cookie(response, raw_session)
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke the current session and clear the cookie."""
    raw = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if raw:
        delete_session(db, raw)
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME, path="/")
    return Response(status_code=204)


class StravaConnectionStatus(BaseModel):
    connected: bool
    athlete_id: Optional[int] = None
    scope: Optional[str] = None
    expires_at: Optional[int] = None


@router.get("/auth/strava/login")
def strava_login():
    """Redirects user to Strava OAuth page."""
    return RedirectResponse(get_strava_port().get_auth_url())


@router.get("/auth/strava/status", response_model=StravaConnectionStatus)
def strava_status(db: Session = Depends(get_db)) -> StravaConnectionStatus:
    """Reports whether a Strava account is linked, and surfaces the athlete id and token scope."""
    account = db.execute(select(StravaAccount)).scalars().first()
    if account is None:
        return StravaConnectionStatus(connected=False)
    return StravaConnectionStatus(
        connected=True,
        athlete_id=account.strava_athlete_id,
        scope=account.scope,
        expires_at=account.expires_at,
    )


@router.get("/auth/strava/callback")
async def strava_callback(
    code: str = Query(..., description="Auth code from Strava"),
    db: Session = Depends(get_db),
):
    """Exchanges code for tokens and creates User/StravaAccount if needed."""
    port = get_strava_port()
    try:
        tokens = await port.exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to exchange token: {str(e)}")

    athlete_data = tokens.athlete or {}
    athlete_id = athlete_data.get("id")

    if not athlete_id:
        raise HTTPException(status_code=400, detail="No athlete ID in response")

    stmt = select(StravaAccount).where(StravaAccount.strava_athlete_id == athlete_id)
    strava_account = db.execute(stmt).scalars().first()

    if strava_account:
        strava_account.access_token = tokens.access_token
        strava_account.refresh_token = tokens.refresh_token
        strava_account.expires_at = tokens.expires_at
        strava_account.scope = "read,activity:read_all,profile:read_all"
    else:
        new_user = User(email=None)
        db.add(new_user)
        db.flush()

        strava_account = StravaAccount(
            user_id=new_user.id,
            strava_athlete_id=athlete_id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scope="read,activity:read_all,profile:read_all",
        )
        db.add(strava_account)

    db.commit()

    return RedirectResponse(url=f"{settings.APP_BASE_URL}?connected=true")
