from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.db.session import get_db
from app.services.strava.client import strava_client
from app.models import User, StravaAccount

router = APIRouter()

@router.get("/auth/strava/login")
def strava_login():
    """
    Redirects user to Strava OAuth page.
    """
    url = strava_client.get_auth_url()
    return RedirectResponse(url)

@router.get("/auth/strava/callback")
async def strava_callback(
    code: str = Query(..., description="Auth code from Strava"),
    db: Session = Depends(get_db)
):
    """
    Exchanges code for tokens and creates User/StravaAccount if needed.
    """
    try:
        # 1. Exchange User Code
        token_data = await strava_client.exchange_code_for_token(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to exchange token: {str(e)}")

    athlete_data = token_data.get("athlete", {})
    athlete_id = athlete_data.get("id")
    
    if not athlete_id:
        raise HTTPException(status_code=400, detail="No athlete ID in response")

    # 2. Check if StravaAccount exists
    stmt = select(StravaAccount).where(StravaAccount.strava_athlete_id == athlete_id)
    strava_account = db.execute(stmt).scalars().first()

    if strava_account:
        # Update tokens
        strava_account.access_token = token_data["access_token"]
        strava_account.refresh_token = token_data["refresh_token"]
        strava_account.expires_at = token_data["expires_at"]
        strava_account.scope = "read,activity:read_all,profile:read_all" # assumed
    else:
        # Create User first (Local-first MVP: create implicitly)
        # Check if we should link to an existing user? 
        # For simplicity, creates a new user per unique Strava account if not found.
        new_user = User(email=None)
        db.add(new_user)
        db.flush() # get ID

        strava_account = StravaAccount(
            user_id=new_user.id,
            strava_athlete_id=athlete_id,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=token_data["expires_at"],
            scope="read,activity:read_all,profile:read_all"
        )
        db.add(strava_account)

    db.commit()

    # Redirect to frontend dashboard
    return RedirectResponse(url=f"{settings.APP_BASE_URL}?connected=true")
