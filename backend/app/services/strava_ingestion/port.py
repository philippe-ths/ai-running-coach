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

    def get_auth_url(self, state: str | None = None) -> str: ...

    async def exchange_code(self, code: str) -> Tokens: ...

    async def refresh_token(self, refresh_token: str) -> Tokens: ...

    async def list_recent_activities(
        self,
        access_token: str,
        since: datetime,
        per_page: int = 50,
    ) -> list[dict]: ...

    async def list_activities_page(
        self,
        access_token: str,
        *,
        after: int,
        before: int | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """Return a single page of activities in the (after, before) epoch window.

        Newest first. Used by the historical import (#239) to walk a runner's
        history backward in time one page at a time, advancing `before` to the
        oldest activity seen so each batch is bounded and the job is resumable.
        """
        ...

    async def get_activity(self, access_token: str, activity_id: int) -> dict: ...

    async def get_activity_streams(
        self,
        access_token: str,
        activity_id: int,
        stream_types: list[str],
    ) -> dict | None: ...

    async def get_athlete_zones(self, access_token: str) -> dict | None:
        """Return the athlete's configured zones (HR/power), or None on failure.

        The raw Strava `/athlete/zones` payload; callers extract what they need.
        Requires the `profile:read_all` scope (already requested at auth).
        """
        ...
