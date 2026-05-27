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
