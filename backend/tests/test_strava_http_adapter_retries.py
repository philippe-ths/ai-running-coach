"""Retry behaviour for HTTPStravaAdapter.

The Phase 1 deployment runs ingestion through this adapter. A single 429
used to fail the pipeline job entirely; the next run for that activity
would only happen on the next webhook or manual sync. Acceptance:
- A transient 429 (short Retry-After) is absorbed inline with a bounded retry.
- A 429 whose Retry-After exceeds the small inline ceiling fails fast with a
  typed StravaRateLimited carrying the true Retry-After, rather than sleeping a
  worker for the full (up to 15-minute) Strava window (#602). The live request
  path turns that into an HTTP 429 "retry shortly"; background jobs let the
  Strava budget gate absorb the wait by rescheduling.
- When the bounded inline 429 retries are exhausted, StravaRateLimited is
  raised.
- 5xx retries are bounded with exponential backoff, then the underlying
  httpx.HTTPStatusError surfaces.
- 4xx other than 429 propagates immediately (auth / bad request will not
  get better on retry).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.strava_ingestion import HTTPStravaAdapter, StravaRateLimited


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
    async def test_large_retry_after_fails_fast(self, monkeypatch, _no_real_sleep):
        """Strava sometimes sends Retry-After minutes/hours away. Rather than
        sleep a worker for the full window, fail fast with a typed error that
        carries the true Retry-After so the caller can react (#602)."""
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "3600"}, json={}),
                httpx.Response(200, json=[]),  # must NOT be consumed
            ],
        )

        with pytest.raises(StravaRateLimited) as exc_info:
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

        # True Retry-After surfaced uncapped; no inline sleep; no wasted retry.
        assert exc_info.value.retry_after == 3600
        _no_real_sleep.assert_not_called()
        assert len(recorder.requests) == 1

    @pytest.mark.asyncio
    async def test_exhausted_429_retries_raise_rate_limited(
        self, monkeypatch, _no_real_sleep
    ):
        """A run of short-Retry-After 429s is retried inline up to the bound,
        then raises StravaRateLimited once the retries are used up."""
        recorder = _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                httpx.Response(429, headers={"Retry-After": "1"}, json={}),
            ],
        )

        with pytest.raises(StravaRateLimited) as exc_info:
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

        assert exc_info.value.retry_after == 1
        # initial attempt + 2 bounded retries = 3 requests, 2 inline sleeps.
        assert len(recorder.requests) == 3
        assert _no_real_sleep.call_count == 2

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


class TestGetAthleteZonesRetries:
    @pytest.mark.asyncio
    async def test_rate_limit_is_swallowed_as_none(self, monkeypatch, _no_real_sleep):
        """Zones are a non-fatal enhancement to time-in-zone, never a sync
        prerequisite; a rate limit must degrade to None (fall back to %-of-max),
        not fail the sync."""
        _install_seq_transport(
            monkeypatch,
            [
                httpx.Response(429, headers={"Retry-After": "3600"}, json={}),
            ],
        )

        result = await HTTPStravaAdapter().get_athlete_zones(access_token="abc")

        assert result is None


class TestHttpTimeout:
    """ReadTimeout must propagate so the job-level handler can log and react."""

    @pytest.mark.asyncio
    async def test_read_timeout_propagates_from_list_activities(
        self, monkeypatch, _no_real_sleep
    ):
        original_init = httpx.AsyncClient.__init__

        async def _timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_timeout_handler)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

        with pytest.raises(httpx.ReadTimeout):
            await HTTPStravaAdapter().list_recent_activities(
                access_token="abc",
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_read_timeout_propagates_from_get_activity(
        self, monkeypatch, _no_real_sleep
    ):
        original_init = httpx.AsyncClient.__init__

        async def _timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_timeout_handler)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

        with pytest.raises(httpx.ReadTimeout):
            await HTTPStravaAdapter().get_activity(access_token="abc", activity_id=42)
