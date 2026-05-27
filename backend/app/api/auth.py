from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.db.session import get_db
from app.models import User, StravaAccount
from app.services.strava_ingestion import get_strava_port

router = APIRouter()


@router.get("/auth/strava/login")
def strava_login():
    """Redirects user to Strava OAuth page."""
    return RedirectResponse(get_strava_port().get_auth_url())


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
