import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.services.strava_ingestion.port import Tokens

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://www.strava.com/oauth/token"
_BASE_URL = "https://www.strava.com/api/v3"


class HTTPStravaAdapter:
    """Production Strava adapter. Pure HTTP, no DB access."""

    def get_auth_url(self) -> str:
        params = {
            "client_id": settings.STRAVA_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.STRAVA_REDIRECT_URI,
            "approval_prompt": "force",
            "scope": "read,activity:read_all,profile:read_all",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"https://www.strava.com/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> Tokens:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _OAUTH_URL,
                data={
                    "client_id": settings.STRAVA_CLIENT_ID,
                    "client_secret": settings.STRAVA_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            data = response.json()
        return Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            athlete=data.get("athlete"),
        )

    async def refresh_token(self, refresh_token: str) -> Tokens:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _OAUTH_URL,
                data={
                    "client_id": settings.STRAVA_CLIENT_ID,
                    "client_secret": settings.STRAVA_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            data = response.json()
        return Tokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )

    async def list_recent_activities(
        self,
        access_token: str,
        since: datetime,
        per_page: int = 50,
    ) -> list[dict]:
        params = {
            "page": 1,
            "per_page": per_page,
            "after": int(since.timestamp()),
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/athlete/activities",
                headers=headers,
                params=params,
            )
            if response.status_code == 429:
                logger.error("Strava Rate Limit Exceeded")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(
                    "Strava API Error: %s - %s", e.response.status_code, e.response.text
                )
                if e.response.status_code == 403:
                    logger.error(
                        "Missing Scopes: Ensure 'activity:read_all' is granted."
                    )
                raise
            return response.json()

    async def get_activity(self, access_token: str, activity_id: int) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/activities/{activity_id}",
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def get_activity_streams(
        self,
        access_token: str,
        activity_id: int,
        stream_types: list[str],
    ) -> dict | None:
        keys = ",".join(stream_types)
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/activities/{activity_id}/streams/{keys}?key_by_type=true",
                headers=headers,
            )
            if response.status_code == 429:
                logger.error("Strava Rate Limit Exceeded (Streams)")
            try:
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error("Strava Stream Error: %s", e.response.status_code)
                return None
