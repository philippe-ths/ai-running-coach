from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional

from app.core.config import settings
from app.db.session import get_db
from app.models import User, StravaAccount
from app.services.strava_ingestion import get_strava_port

router = APIRouter()


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
        # A brand-new Strava athlete. In single-owner prod this branch is
        # dormant (the owner is already connected, so the token-refresh branch
        # above fires). Under Phase 2 (ADR 0022) email is non-null, so attach to
        # the single existing app user when there is exactly one (the realistic
        # single-owner first-connect), else create a user with a unique
        # placeholder email so the non-null constraint holds.
        # TODO(P2.1): thread the verified Clerk user through the OAuth `state`
        # param so a new Strava account links to the signed-in user, not a
        # placeholder. The Strava callback is a direct browser redirect from
        # Strava and carries no Clerk session, so true multi-user linking needs
        # a server-verifiable state token. Tracked as a follow-up issue.
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
