"""Tests for AnthropicClient.generate_json's explicit timeout + bounded retry.

The SDK's default timeout is 600 seconds, so a single hung call would lock
an RQ worker for ten minutes. Acceptance:
- Calls carry an explicit timeout (60s).
- One retry with backoff on transient failures (timeout, connection, 5xx).
- Non-retriable errors (4xx other than 429) propagate immediately.
- After the retry is exhausted, the underlying error propagates so the
  caller's fallback path (services/coach/service.py is_fallback=True) fires.
"""

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

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
