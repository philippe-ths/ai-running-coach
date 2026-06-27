from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.core.config import settings
from app.core.clerk_auth import require_current_user
from app.core.oauth_state import encode_state, decode_state
from app.db.session import get_db
from app.models import User, StravaAccount
from app.services.strava_ingestion import get_strava_port

router = APIRouter()


class StravaConnectionStatus(BaseModel):
    connected: bool
    athlete_id: Optional[int] = None
    scope: Optional[str] = None
    expires_at: Optional[int] = None


def _resolve_user_from_state(db: Session, state: Optional[str]) -> Optional[User]:
    """Return the user a valid OAuth `state` binds, else ``None`` (#469).

    Returns ``None`` for an absent, malformed, tampered, or expired state, and
    also when the bound user_id no longer exists, so the caller falls back to
    the legacy single-owner resolution.
    """
    if not state:
        return None
    user_id = decode_state(state)
    if user_id is None:
        return None
    return db.get(User, user_id)


@router.get("/auth/strava/login")
def strava_login(user: User = Depends(require_current_user)):
    """Redirects the signed-in user to the Strava OAuth page.

    Gated on the session so the OAuth `state` can carry the authenticated
    user_id through to the callback (#469); the callback is a bare browser
    redirect from Strava with no session, so identity rides in the signed state.
    """
    state = encode_state(user.id)
    return RedirectResponse(get_strava_port().get_auth_url(state=state))


@router.get("/auth/strava/status", response_model=StravaConnectionStatus)
def strava_status(db: Session = Depends(get_db)) -> StravaConnectionStatus:
    """Reports whether a Strava account is linked, and surfaces the athlete id and token scope.

    NOTE: login + callback now thread the signed-in user through a signed OAuth
    `state` so a new account links to the right user (#469), but this status
    READ stays single-user (the first linked account) -- it is session-exempt
    (ADR 0022 exempts /api/auth/*) and returns no training data, only the
    connection's athlete id and scope. A per-user status read is part of the
    remaining Strava-linking follow-up.
    """
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
    state: Optional[str] = Query(None, description="Signed state binding the signed-in user (#469)"),
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
        # A brand-new Strava athlete. Resolve the app user to attach it to.
        #
        # Multi-user (#469): the signed-in user rode through the OAuth flow in
        # the `state` token, so a valid state links the account to exactly that
        # user. This is the fix for mis-linking a second user's Strava account.
        #
        # Back-compat fallback: when `state` is absent or invalid (a stale link,
        # a direct callback hit, or single-owner local dev with no state secret)
        # fall through to the legacy single-owner rule -- attach to the single
        # existing app user when there is exactly one, else mint a placeholder
        # email so the non-null constraint holds. The single-owner prod path is
        # unchanged. (A stricter prod-only reject of an invalid-but-present state
        # is a possible follow-up; see the PR for #469.)
        new_user = _resolve_user_from_state(db, state)
        if new_user is None:
            existing_users = db.execute(select(User)).scalars().all()
            if len(existing_users) == 1:
                new_user = existing_users[0]
            else:
                new_user = User(email=f"strava-{athlete_id}@placeholder.invalid")
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
