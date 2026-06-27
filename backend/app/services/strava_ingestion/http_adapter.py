import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from app.core.config import settings
from app.services.strava_ingestion.port import Tokens
from app.services.strava_ingestion.strava_budget import record as _record_strava_call

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://www.strava.com/oauth/token"
_BASE_URL = "https://www.strava.com/api/v3"

# Retry tuning. The pipeline job and the polling fallback both flow through
# this adapter; a single 429 from Strava used to fail the whole pipeline.
_MAX_RETRIES = 2  # 2 retries after the initial attempt = up to 3 calls total
_BASE_BACKOFF_S = 1.0
# Cap an honoured Retry-After so a misbehaving header cannot freeze a worker.
_MAX_RETRY_AFTER_S = 60.0

# Hard cap on pages walked by list_recent_activities. At per_page=50 this bounds
# a single call to 2000 activities, enough for a full-history backfill of a
# realistic single user. If the cap is hit the result is truncated; we log so a
# silent truncation never masquerades as "fetched everything". See #109.
_MAX_PAGES = 40

# Hard cap on how long we wait for a single Strava response. 30 s is well
# above typical latency but short enough to free a worker within one polling
# interval. Without this, a slow Strava response hangs the job queue forever.
_HTTP_TIMEOUT_S = 30.0


def _parse_retry_after(response: httpx.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return min(float(raw), _MAX_RETRY_AFTER_S)
        except ValueError:
            pass
    return min(default, _MAX_RETRY_AFTER_S)


async def _send_with_retry(
    request_fn: Callable[[], Awaitable[httpx.Response]],
    *,
    label: str,
) -> httpx.Response:
    """Run request_fn() with bounded retries on 429 + 5xx.

    Returns the final response. Raising is left to the caller via
    response.raise_for_status() so each method keeps its existing error
    semantics (e.g. get_activity_streams returns None on failure).
    """
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        response = await request_fn()
        # Every actual request — including a retry — counts against Strava's shared
        # per-application rate limit, so record each one against the global budget
        # (#544). This makes the counter honest about ALL metered traffic (webhook
        # ingest included); only the background jobs gate on it, never the live path.
        _record_strava_call()
        if response.status_code == 429 and attempt < _MAX_RETRIES:
            wait = _parse_retry_after(response, _BASE_BACKOFF_S * (2 ** attempt))
            logger.warning(
                "strava_429_retry",
                extra={"label": label, "attempt": attempt + 1, "wait_s": wait},
            )
            await asyncio.sleep(wait)
            continue
        if 500 <= response.status_code < 600 and attempt < _MAX_RETRIES:
            wait = _BASE_BACKOFF_S * (2 ** attempt)
            logger.warning(
                "strava_5xx_retry",
                extra={
                    "label": label,
                    "attempt": attempt + 1,
                    "status": response.status_code,
                    "wait_s": wait,
                },
            )
            await asyncio.sleep(wait)
            continue
        return response
    assert response is not None  # loop runs at least once
    return response


class HTTPStravaAdapter:
    """Production Strava adapter. Pure HTTP, no DB access."""

    def get_auth_url(self, state: str | None = None) -> str:
        params = {
            "client_id": settings.STRAVA_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.STRAVA_REDIRECT_URI,
            "approval_prompt": "force",
            "scope": "read,activity:read_all,profile:read_all",
        }
        # The signed-in user's OAuth state (#469); Strava echoes it back on the
        # callback so the new StravaAccount links to that user. The token is
        # base64url + dotted, so it is URL-safe for this un-encoded builder.
        if state:
            params["state"] = state
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"https://www.strava.com/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> Tokens:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
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
        """Return every activity since `since`, walking pages until exhausted.

        Strava paginates `/athlete/activities`; a page shorter than `per_page`
        is the last one. Fetching only page 1 (the old behaviour) silently
        dropped any activity beyond the first `per_page` in the window, which is
        what left the #109 gap unrecoverable by both polling and manual sync.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        after = int(since.timestamp())
        activities: list[dict] = []
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            for page in range(1, _MAX_PAGES + 1):
                params = {"page": page, "per_page": per_page, "after": after}
                response = await _send_with_retry(
                    lambda: client.get(
                        f"{_BASE_URL}/athlete/activities",
                        headers=headers,
                        params=params,
                    ),
                    label="list_recent_activities",
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    logger.error(
                        "Strava API Error: %s - %s",
                        e.response.status_code,
                        e.response.text,
                    )
                    if e.response.status_code == 403:
                        logger.error(
                            "Missing Scopes: Ensure 'activity:read_all' is granted."
                        )
                    raise
                batch = response.json()
                activities.extend(batch)
                if len(batch) < per_page:
                    break
            else:
                logger.warning(
                    "list_recent_activities hit the %s-page cap; result may be "
                    "truncated (after=%s, per_page=%s)",
                    _MAX_PAGES,
                    after,
                    per_page,
                )
        return activities

    async def list_activities_page(
        self,
        access_token: str,
        *,
        after: int,
        before: int | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """Fetch one page of `/athlete/activities` in the (after, before) window.

        Strava returns activities newest-first and treats `before`/`after` as
        exclusive/inclusive epoch bounds. The historical import (#239) walks
        backward by re-calling with `before` set to the oldest activity's start
        epoch, so each batch is a bounded, resumable step rather than the full
        page-walk that `list_recent_activities` does.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        params: dict[str, int] = {"page": page, "per_page": per_page, "after": after}
        if before is not None:
            params["before"] = before
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            response = await _send_with_retry(
                lambda: client.get(
                    f"{_BASE_URL}/athlete/activities",
                    headers=headers,
                    params=params,
                ),
                label="list_activities_page",
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(
                    "Strava API Error: %s - %s",
                    e.response.status_code,
                    e.response.text,
                )
                if e.response.status_code == 403:
                    logger.error(
                        "Missing Scopes: Ensure 'activity:read_all' is granted."
                    )
                raise
            return response.json()

    async def get_activity(self, access_token: str, activity_id: int) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            response = await _send_with_retry(
                lambda: client.get(
                    f"{_BASE_URL}/activities/{activity_id}",
                    headers=headers,
                ),
                label="get_activity",
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
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            response = await _send_with_retry(
                lambda: client.get(
                    f"{_BASE_URL}/activities/{activity_id}/streams/{keys}?key_by_type=true",
                    headers=headers,
                ),
                label="get_activity_streams",
            )
            try:
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error("Strava Stream Error: %s", e.response.status_code)
                return None

    async def get_athlete_zones(self, access_token: str) -> dict | None:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            response = await _send_with_retry(
                lambda: client.get(f"{_BASE_URL}/athlete/zones", headers=headers),
                label="get_athlete_zones",
            )
            try:
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                # Non-fatal: zones are an enhancement to the time-in-zone metric,
                # not a sync prerequisite. A 403 means the profile:read_all scope
                # was not granted; we log and fall back to the %max scheme.
                logger.warning("Strava Zones Error: %s", e.response.status_code)
                return None
