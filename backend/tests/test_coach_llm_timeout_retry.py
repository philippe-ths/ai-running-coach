"""Tests for AnthropicClient.generate_json's explicit timeout + bounded retry,
and for generate_coach_message's streaming disconnect handling (#302).

The SDK's default timeout is 600 seconds, so a single hung call would lock
an RQ worker for ten minutes. Acceptance:
- Calls carry an explicit timeout (60s).
- One retry with backoff on transient failures (timeout, connection, 5xx).
- Non-retriable errors (4xx other than 429) propagate immediately.
- After the retry is exhausted, the underlying error propagates so the
  caller's fallback path (services/coach/service.py is_fallback=True) fires.
- A mid-stream httpx.RemoteProtocolError (peer closed connection) is treated
  as a transient transport failure in generate_coach_message — retried once,
  and propagates after retry exhaustion so the service fallback path fires.
  It must NOT crash the job with an uncaught raw exception.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from app.services.coach.llm import AnthropicClient


def _ok_response(text: str = '{"key_takeaways":[{"text":"ok"}],"next_steps":[{"action":"a","details":"d","why":"w"}]}'):
    """Build a minimal stand-in for an anthropic Message response."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _make_status_error(status: int) -> anthropic.APIStatusError:
    """Build a real APIStatusError instance with the requested status code."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status, request=request)
    return anthropic.APIStatusError(
        f"status {status}", response=response, body=None
    )


@pytest.mark.asyncio
async def test_passes_explicit_timeout_to_sdk():
    client = AnthropicClient(api_key="k", model="m")
    fake_create = AsyncMock(return_value=_ok_response())
    client.client.messages.create = fake_create

    await client.generate_json("sys", "user", max_tokens=128)

    fake_create.assert_called_once()
    kwargs = fake_create.call_args.kwargs
    assert "timeout" in kwargs
    assert isinstance(kwargs["timeout"], (int, float))
    assert 0 < kwargs["timeout"] <= 120


@pytest.mark.asyncio
async def test_returns_first_attempt_when_call_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    fake_create = AsyncMock(return_value=_ok_response("hello"))
    client.client.messages.create = fake_create

    result = await client.generate_json("sys", "user")

    assert result == "hello"
    assert fake_create.call_count == 1


@pytest.mark.asyncio
async def test_retries_once_on_timeout_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    timeout_err = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake_create = AsyncMock(side_effect=[timeout_err, _ok_response("ok")])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_json("sys", "user")

    assert result == "ok"
    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_retries_once_on_connection_error_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    conn_err = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake_create = AsyncMock(side_effect=[conn_err, _ok_response("ok")])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_json("sys", "user")

    assert result == "ok"
    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_retries_once_on_5xx_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    server_err = _make_status_error(503)
    fake_create = AsyncMock(side_effect=[server_err, _ok_response("ok")])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_json("sys", "user")

    assert result == "ok"
    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_does_not_retry_on_4xx_non_429():
    client = AnthropicClient(api_key="k", model="m")
    bad_request = _make_status_error(400)
    fake_create = AsyncMock(side_effect=bad_request)
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.APIStatusError):
            await client.generate_json("sys", "user")

    assert fake_create.call_count == 1


@pytest.mark.asyncio
async def test_propagates_underlying_exception_after_retry_exhausted():
    client = AnthropicClient(api_key="k", model="m")
    timeout_err = anthropic.APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    fake_create = AsyncMock(side_effect=[timeout_err, timeout_err])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.APITimeoutError):
            await client.generate_json("sys", "user")

    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_propagates_5xx_after_retry_exhausted():
    client = AnthropicClient(api_key="k", model="m")
    server_err = _make_status_error(503)
    fake_create = AsyncMock(side_effect=[server_err, server_err])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.APIStatusError):
            await client.generate_json("sys", "user")

    assert fake_create.call_count == 2


# ---------------------------------------------------------------------------
# generate_coach_message — streaming disconnect (#302)
#
# httpx.RemoteProtocolError (peer closed connection without completing the
# chunked stream) is raised from stream.get_final_message() inside the async
# context manager. It is NOT a subclass of anthropic.APIConnectionError, so
# the existing except clause does not catch it. The fix must treat it as a
# transient transport failure: retry once, then propagate so the service
# fallback path (is_fallback=True) fires — exactly like APIConnectionError.
# ---------------------------------------------------------------------------

def _make_remote_protocol_error() -> httpx.RemoteProtocolError:
    """Build a real httpx.RemoteProtocolError as the peer-disconnect case."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body"
        " (incomplete chunked read)",
        request=request,
    )


def _make_ok_message_result():
    """Minimal stand-in for anthropic.types.Message returned by get_final_message."""
    block = SimpleNamespace(type="text", text="Well run today.")
    return SimpleNamespace(content=[block], stop_reason="end_turn")


def _make_streaming_ctx(side_effect):
    """Return a factory that produces an async context manager whose
    get_final_message() raises or returns according to *side_effect*
    (a list, consumed one item per call — same semantics as AsyncMock
    side_effect)."""
    calls = iter(side_effect)

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        outcome = next(calls)
        stream = MagicMock()
        if isinstance(outcome, BaseException):
            stream.get_final_message = AsyncMock(side_effect=outcome)
        else:
            stream.get_final_message = AsyncMock(return_value=outcome)
        yield stream

    return _ctx


@pytest.mark.asyncio
async def test_generate_coach_message_retries_on_remote_protocol_error_then_succeeds():
    """A mid-stream RemoteProtocolError on the first attempt triggers one retry
    and the successful second attempt is returned."""
    client = AnthropicClient(api_key="k", model="m")
    ok = _make_ok_message_result()
    err = _make_remote_protocol_error()

    client.client.messages.stream = _make_streaming_ctx([err, ok])

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_coach_message(
            system="sys", user="user", tools=[]
        )

    assert result.stop_reason == "end_turn"
    assert len(result.content_blocks) == 1


@pytest.mark.asyncio
async def test_generate_coach_message_propagates_remote_protocol_error_after_retry_exhausted():
    """After the single retry is also a RemoteProtocolError, the exception
    propagates so the caller's fallback path (is_fallback=True) fires.
    It must NOT be swallowed and silently dropped."""
    client = AnthropicClient(api_key="k", model="m")
    err1 = _make_remote_protocol_error()
    err2 = _make_remote_protocol_error()

    client.client.messages.stream = _make_streaming_ctx([err1, err2])

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(httpx.RemoteProtocolError):
            await client.generate_coach_message(
                system="sys", user="user", tools=[]
            )


@pytest.mark.asyncio
async def test_generate_coach_message_logs_warning_on_remote_protocol_error(caplog):
    """A RemoteProtocolError is logged at WARNING with the transient-failure key,
    not silently dropped or re-raised as an unknown error."""
    import logging

    client = AnthropicClient(api_key="k", model="m")
    ok = _make_ok_message_result()
    err = _make_remote_protocol_error()

    client.client.messages.stream = _make_streaming_ctx([err, ok])

    with patch("asyncio.sleep", new=AsyncMock()):
        with caplog.at_level(logging.WARNING, logger="app.services.coach.llm"):
            await client.generate_coach_message(
                system="sys", user="user", tools=[]
            )

    assert any("transient" in r.message.lower() for r in caplog.records)
