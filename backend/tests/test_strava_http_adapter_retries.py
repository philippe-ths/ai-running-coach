"""Retry behaviour for HTTPStravaAdapter.

The Phase 1 deployment runs ingestion through this adapter. A single 429
used to fail the pipeline job entirely; the next run for that activity
would only happen on the next webhook or manual sync. Acceptance:
- 429 honours Retry-After (capped at a ceiling so a misbehaving header
  cannot freeze a worker).
- 5xx retries are bounded with exponential backoff.
- 4xx other than 429 propagates immediately (auth / bad request will not
  get better on retry).
- After retries are exhausted, the underlying httpx.HTTPStatusError
  surfaces so the polling job's except-Exception still catches it.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.strava_ingestion import HTTPStravaAdapter


class _SeqRecorder:
    """Mock transport that returns the next queued response on each call."""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("transport called more times than responses queued")
        return self.responses.pop(0)


def _install_seq_transport(
    monkeypatch, responses: list[httpx.Response]
) -> _SeqRecorder:
    recorder = _SeqRecorder(responses)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return recorder


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Patch the adapter's sleep so retry tests stay sub-second."""
    with patch(
        "app.services.strava_ingestion.http_adapter.asyncio.sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        yield mock_sleep


class TestListRecentActivitiesRetries:
    @pytest.mark.asyncio
    async def test_retries_after_429_then_succeeds(self, monkeypatch, _no_real_sleep):
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                httpx.Response(200, json=[{"id": 1}]),
            ],
        )

        result = await HTTPStravaAdapter().list_recent_activities(
            access_token="abc",
            since=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert result == [{"id": 1}]
        assert len(recorder.requests) == 2

    @pytest.mark.asyncio
    async def test_retries_after_503_then_succeeds(self, monkeypatch, _no_real_sleep):
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(503, json={}),
                httpx.Response(200, json=[]),
            ],
        )

        result = await HTTPStravaAdapter().list_recent_activities(
            access_token="abc",
            since=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert result == []
        assert len(recorder.requests) == 2

    @pytest.mark.asyncio
    async def test_429_is_capped_at_ceiling(self, monkeypatch, _no_real_sleep):
        """Strava sometimes sends Retry-After hours away. We cap so a single
        bad header cannot freeze the worker for an hour."""
        _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "3600"}, json={}),
                httpx.Response(200, json=[]),
            ],
        )

        await HTTPStravaAdapter().list_recent_activities(
            access_token="abc",
            since=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        # The sleep call must have been bounded; pull the last call's first arg.
        _no_real_sleep.assert_called()
        last_wait = _no_real_sleep.call_args.args[0]
        assert last_wait <= 120, f"Retry-After cap not honoured (slept {last_wait}s)"

    @pytest.mark.asyncio
    async def test_raises_after_bounded_5xx_retries(self, monkeypatch, _no_real_sleep):
        _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(503, json={}),
                httpx.Response(503, json={}),
                httpx.Response(503, json={}),
                httpx.Response(503, json={}),  # extra: must not be consumed
            ],
        )

        with pytest.raises(httpx.HTTPStatusError):
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_does_not_retry_on_401(self, monkeypatch, _no_real_sleep):
        """The token-refresh path is handled at a higher level; the adapter
        must surface 401 immediately so the caller can refresh and retry."""
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(401, json={"message": "Unauthorized"}),
            ],
        )

        with pytest.raises(httpx.HTTPStatusError):
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

        # Exactly one call; no retry.
        assert len(recorder.requests) == 1

    @pytest.mark.asyncio
    async def test_does_not_retry_on_403(self, monkeypatch, _no_real_sleep):
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(403, json={"message": "Forbidden"}),
            ],
        )

        with pytest.raises(httpx.HTTPStatusError):
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

        assert len(recorder.requests) == 1


class TestGetActivityRetries:
    @pytest.mark.asyncio
    async def test_retries_after_429(self, monkeypatch, _no_real_sleep):
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                httpx.Response(200, json={"id": 42}),
            ],
        )

        result = await HTTPStravaAdapter().get_activity(
            access_token="abc", activity_id=42
        )

        assert result == {"id": 42}
        assert len(recorder.requests) == 2


class TestGetActivityStreamsRetries:
    @pytest.mark.asyncio
    async def test_retries_after_429(self, monkeypatch, _no_real_sleep):
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                httpx.Response(200, json={"time": {"data": []}}),
            ],
        )

        result = await HTTPStravaAdapter().get_activity_streams(
            access_token="abc",
            activity_id=42,
            stream_types=["time"],
        )

        assert result == {"time": {"data": []}}
        assert len(recorder.requests) == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_non_retriable_error(self, monkeypatch, _no_real_sleep):
        """get_activity_streams already swallows errors and returns None.
        Behaviour must be preserved after retries are exhausted."""
        _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(404, json={"message": "Not Found"}),
            ],
        )

        result = await HTTPStravaAdapter().get_activity_streams(
            access_token="abc",
            activity_id=42,
            stream_types=["time"],
        )

        assert result is None
