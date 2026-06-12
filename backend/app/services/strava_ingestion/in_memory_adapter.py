from datetime import datetime, timezone

from app.services.strava_ingestion.port import Tokens


def _start_epoch(raw: dict) -> int:
    dt = datetime.strptime(raw["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return int(dt.timestamp())


class InMemoryStravaAdapter:
    """In-memory Strava adapter for tests. Seedable with canned data."""

    def __init__(self) -> None:
        self.activities: list[dict] = []
        self.streams: dict[int, dict] = {}
        self.next_tokens: Tokens | None = None
        self.exchange_response: Tokens | None = None
        # Call log for assertions.
        self.refresh_calls: list[str] = []
        self.exchange_calls: list[str] = []
        self.list_calls: list[tuple[str, datetime, int]] = []
        self.page_calls: list[tuple[int, int | None, int, int]] = []
        self.stream_calls: list[tuple[int, list[str]]] = []
        self.activity_calls: list[int] = []

    # Seeding helpers --------------------------------------------------------

    def seed_activities(self, activities: list[dict]) -> None:
        self.activities = list(activities)

    def seed_streams(self, activity_id: int, streams: dict) -> None:
        self.streams[activity_id] = streams

    def seed_refresh_response(self, tokens: Tokens) -> None:
        self.next_tokens = tokens

    def seed_exchange_response(self, tokens: Tokens) -> None:
        self.exchange_response = tokens

    # Port surface -----------------------------------------------------------

    def get_auth_url(self) -> str:
        return "https://example.test/strava/authorize"

    async def exchange_code(self, code: str) -> Tokens:
        self.exchange_calls.append(code)
        if self.exchange_response is None:
            raise AssertionError(
                "InMemoryStravaAdapter.exchange_code called without a seeded response"
            )
        return self.exchange_response

    async def refresh_token(self, refresh_token: str) -> Tokens:
        self.refresh_calls.append(refresh_token)
        if self.next_tokens is None:
            raise AssertionError(
                "InMemoryStravaAdapter.refresh_token called without a seeded response"
            )
        return self.next_tokens

    async def list_recent_activities(
        self,
        access_token: str,
        since: datetime,
        per_page: int = 50,
    ) -> list[dict]:
        self.list_calls.append((access_token, since, per_page))
        return list(self.activities)

    async def list_activities_page(
        self,
        access_token: str,
        *,
        after: int,
        before: int | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        self.page_calls.append((after, before, page, per_page))
        # Mirror Strava: inclusive `after`, exclusive `before`, newest first.
        window = [
            raw
            for raw in self.activities
            if _start_epoch(raw) >= after
            and (before is None or _start_epoch(raw) < before)
        ]
        window.sort(key=_start_epoch, reverse=True)
        start = (page - 1) * per_page
        return window[start : start + per_page]

    async def get_activity(self, access_token: str, activity_id: int) -> dict:
        self.activity_calls.append(activity_id)
        for raw in self.activities:
            if raw.get("id") == activity_id:
                return raw
        raise AssertionError(
            f"InMemoryStravaAdapter.get_activity called for unseeded id {activity_id}"
        )

    async def get_activity_streams(
        self,
        access_token: str,
        activity_id: int,
        stream_types: list[str],
    ) -> dict | None:
        self.stream_calls.append((activity_id, list(stream_types)))
        return self.streams.get(activity_id)
