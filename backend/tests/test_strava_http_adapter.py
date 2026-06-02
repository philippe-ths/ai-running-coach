"""Outbound HTTP shape regression tests for HTTPStravaAdapter.

These guard against accidental drift in the bytes we send to Strava — body,
query, URL, headers — even though the deep ingestion module is tested via the
in-memory adapter.
"""

from datetime import datetime, timezone

import httpx
import pytest

from app.services.strava_ingestion import HTTPStravaAdapter


class _RequestRecorder:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def _install_transport(monkeypatch, response: httpx.Response) -> _RequestRecorder:
    recorder = _RequestRecorder(response)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return recorder


@pytest.mark.asyncio
async def test_refresh_token_posts_oauth_body(monkeypatch):
    recorder = _install_transport(
        monkeypatch,
        httpx.Response(
            200,
            json={
                "access_token": "new_a",
                "refresh_token": "new_r",
                "expires_at": 1_700_000_000,
            },
        ),
    )

    tokens = await HTTPStravaAdapter().refresh_token("the_refresh")

    assert tokens.access_token == "new_a"
    assert tokens.refresh_token == "new_r"

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://www.strava.com/oauth/token"
    body = request.content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=the_refresh" in body


@pytest.mark.asyncio
async def test_list_recent_activities_uses_after_param_and_bearer_header(monkeypatch):
    recorder = _install_transport(monkeypatch, httpx.Response(200, json=[]))

    since = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    await HTTPStravaAdapter().list_recent_activities(
        access_token="abc", since=since, per_page=50
    )

    [request] = recorder.requests
    assert request.method == "GET"
    assert request.url.path == "/api/v3/athlete/activities"
    assert request.url.params["after"] == str(int(since.timestamp()))
    assert request.url.params["per_page"] == "50"
    assert request.headers["Authorization"] == "Bearer abc"


class _PagedRecorder:
    """Transport that serves a different page body per `page` query param.

    Ground truth: Strava paginates /athlete/activities and a page shorter than
    per_page is the last one. Pages beyond what's seeded return an empty body.
    """

    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params["page"])
        body = self.pages[page - 1] if page - 1 < len(self.pages) else []
        return httpx.Response(200, json=body)


def _install_paged_transport(monkeypatch, pages: list[list[dict]]) -> _PagedRecorder:
    recorder = _PagedRecorder(pages)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return recorder


@pytest.mark.asyncio
async def test_list_recent_activities_paginates_until_short_page(monkeypatch):
    full_page = [{"id": i} for i in range(50)]
    short_page = [{"id": 100}, {"id": 101}]
    recorder = _install_paged_transport(monkeypatch, [full_page, short_page])

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = await HTTPStravaAdapter().list_recent_activities(
        access_token="abc", since=since, per_page=50
    )

    # Both pages were walked and concatenated.
    assert len(result) == 52
    assert [int(r.url.params["page"]) for r in recorder.requests] == [1, 2]


@pytest.mark.asyncio
async def test_list_recent_activities_single_short_page_makes_one_request(monkeypatch):
    recorder = _install_paged_transport(monkeypatch, [[{"id": 1}, {"id": 2}]])

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = await HTTPStravaAdapter().list_recent_activities(
        access_token="abc", since=since, per_page=50
    )

    assert len(result) == 2
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_list_recent_activities_full_then_empty_page_stops(monkeypatch):
    # An exactly-full page forces a second request; the empty page ends it.
    full_page = [{"id": i} for i in range(50)]
    recorder = _install_paged_transport(monkeypatch, [full_page, []])

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = await HTTPStravaAdapter().list_recent_activities(
        access_token="abc", since=since, per_page=50
    )

    assert len(result) == 50
    assert [int(r.url.params["page"]) for r in recorder.requests] == [1, 2]


@pytest.mark.asyncio
async def test_list_recent_activities_caps_pages_and_truncates(monkeypatch):
    from app.services.strava_ingestion import http_adapter

    # Every page is full, so only the page cap stops the walk.
    always_full = [[{"id": i} for i in range(50)] for _ in range(http_adapter._MAX_PAGES + 5)]
    recorder = _install_paged_transport(monkeypatch, always_full)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = await HTTPStravaAdapter().list_recent_activities(
        access_token="abc", since=since, per_page=50
    )

    assert len(recorder.requests) == http_adapter._MAX_PAGES
    assert len(result) == http_adapter._MAX_PAGES * 50


@pytest.mark.asyncio
async def test_get_activity_streams_uses_key_by_type_and_comma_keys(monkeypatch):
    recorder = _install_transport(
        monkeypatch, httpx.Response(200, json={"time": {"data": []}})
    )

    await HTTPStravaAdapter().get_activity_streams(
        access_token="abc",
        activity_id=12345,
        stream_types=["time", "heartrate"],
    )

    [request] = recorder.requests
    assert request.method == "GET"
    assert request.url.path == "/api/v3/activities/12345/streams/time,heartrate"
    assert request.url.params["key_by_type"] == "true"
    assert request.headers["Authorization"] == "Bearer abc"


@pytest.mark.asyncio
async def test_get_activity_streams_returns_none_on_error(monkeypatch):
    _install_transport(monkeypatch, httpx.Response(404, json={"message": "Not Found"}))

    result = await HTTPStravaAdapter().get_activity_streams(
        access_token="abc", activity_id=42, stream_types=["time"]
    )

    assert result is None


@pytest.mark.asyncio
async def test_exchange_code_returns_tokens_with_athlete(monkeypatch):
    recorder = _install_transport(
        monkeypatch,
        httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": 1_700_000_000,
                "athlete": {"id": 42},
            },
        ),
    )

    tokens = await HTTPStravaAdapter().exchange_code("the_code")

    assert tokens.athlete == {"id": 42}
    [request] = recorder.requests
    body = request.content.decode()
    assert "grant_type=authorization_code" in body
    assert "code=the_code" in body
