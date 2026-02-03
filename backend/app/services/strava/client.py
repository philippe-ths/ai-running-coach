import logging
import time
import httpx
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import StravaAccount

logger = logging.getLogger(__name__)

class StravaClient:
    def __init__(self):
        self.base_url = "https://www.strava.com/api/v3"
        self.oauth_url = "https://www.strava.com/oauth/token"
    
    def get_auth_url(self) -> str:
        """Generates the Strava OAuth URL."""
        params = {
            "client_id": settings.STRAVA_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.STRAVA_REDIRECT_URI,
            "approval_prompt": "force",
            "scope": "read,activity:read_all,profile:read_all"
        }
        # simple query string construction
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://www.strava.com/oauth/authorize?{query}"

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchanges authorization code for access/refresh tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(self.oauth_url, data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code"
            })
            response.raise_for_status()
            return response.json()

    async def ensure_valid_token(self, db: Session, strava_account: StravaAccount) -> str:
        """
        Checks if token is expired (or close to it). 
        If so, refreshes it, updates DB, and returns new access_token.
        """
        # Buffer of 60 seconds
        if strava_account.expires_at > time.time() + 60:
            return strava_account.access_token

        # Refresh needed
        async with httpx.AsyncClient() as client:
            response = await client.post(self.oauth_url, data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": strava_account.refresh_token
            })
            
            # Simple error handling for now - raise if refresh fails
            response.raise_for_status()
            data = response.json()

        # Update DB record
        strava_account.access_token = data["access_token"]
        strava_account.refresh_token = data["refresh_token"]
        strava_account.expires_at = data["expires_at"]
        
        db.add(strava_account)
        db.commit()
        db.refresh(strava_account)
        
        return strava_account.access_token

    async def get_athlete_activities(
        self, 
        access_token: str, 
        after: int = None, 
        before: int = None, 
        page: int = 1, 
        per_page: int = 30
    ) -> list:
        """
        Fetches activities for the authenticated athlete.
        """
        params = {"page": page, "per_page": per_page}
        if after:
            params["after"] = after
        if before:
            params["before"] = before

        headers = {"Authorization": f"Bearer {access_token}"}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/athlete/activities", 
                headers=headers, 
                params=params
            )
            
            # Rate limit logging
            if response.status_code == 429:
                logger.error("Strava Rate Limit Exceeded")
            
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"Strava API Error: {e.response.status_code} - {e.response.text}")
                # Check for scope issues
                if e.response.status_code == 403:
                    logger.error("Missing Scopes: Ensure 'activity:read_all' is granted.")
                raise e
                
            return response.json()

    async def get_activity(self, access_token: str, activity_id: int) -> dict:
        """
        Fetches a single activity detail from Strava.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/activities/{activity_id}",
                headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def get_activity_streams(self, access_token: str, activity_id: int, stream_types: list[str]) -> dict | None:
        """
        Fetches streams for an activity. 
        stream_types: e.g. ['time', 'heartrate', 'velocity_smooth', 'altitude']
        """
        keys = ",".join(stream_types)
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Strava API: /activities/{id}/streams/{keys}
        # key_by_type=true returns object keys, default is array
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/activities/{activity_id}/streams/{keys}?key_by_type=true",
                headers=headers
            )
            
            if response.status_code == 429:
                logger.error("Strava Rate Limit Exceeded (Streams)")

            try:
                response.raise_for_status()
                # Returns a dictionary where keys are stream types because key_by_type=true
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Strava Stream Error: {e.response.status_code}")
                return None

strava_client = StravaClient()
