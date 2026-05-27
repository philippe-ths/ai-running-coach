from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: int
    athlete: dict | None = None


class StravaPort(Protocol):
    """Pure transport interface for Strava. Returns plain data; no DB access."""

    def get_auth_url(self) -> str: ...

    async def exchange_code(self, code: str) -> Tokens: ...

    async def refresh_token(self, refresh_token: str) -> Tokens: ...

    async def list_recent_activities(
        self,
        access_token: str,
        since: datetime,
        per_page: int = 50,
    ) -> list[dict]: ...

    async def get_activity(self, access_token: str, activity_id: int) -> dict: ...

    async def get_activity_streams(
        self,
        access_token: str,
        activity_id: int,
        stream_types: list[str],
    ) -> dict | None: ...
