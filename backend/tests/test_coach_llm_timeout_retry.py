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


# ---------------------------------------------------------------------------
# Anthropic 429 / RateLimitError (#603)
#
# RateLimitError is a subclass of APIStatusError with status_code 429. Because
# 429 < 500, the old code raised it immediately and the coach report silently
# degraded to the deterministic fallback (is_fallback=True). The fix retries a
# 429 with exponential backoff, honoring the Retry-After header (capped) before
# giving up and propagating so the caller's fallback still fires as a last
# resort.
# ---------------------------------------------------------------------------

from app.services.coach import llm as _llm_mod  # noqa: E402


def _make_rate_limit_error(retry_after=None) -> anthropic.RateLimitError:
    """Build a real anthropic.RateLimitError (429), optionally carrying a
    Retry-After header (a string count of seconds, as the API sends it)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    response = httpx.Response(status_code=429, request=request, headers=headers)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    rate_limited = _make_rate_limit_error(retry_after=2)
    fake_create = AsyncMock(side_effect=[rate_limited, _ok_response("ok")])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_json("sys", "user")

    assert result == "ok"
    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_429_honors_retry_after_header():
    client = AnthropicClient(api_key="k", model="m")
    rate_limited = _make_rate_limit_error(retry_after=7)
    fake_create = AsyncMock(side_effect=[rate_limited, _ok_response("ok")])
    client.client.messages.create = fake_create

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", new=sleep_mock):
        await client.generate_json("sys", "user")

    # The single backoff before the retry honored the Retry-After value.
    assert sleep_mock.await_args_list[0].args[0] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_429_caps_absurd_retry_after():
    client = AnthropicClient(api_key="k", model="m")
    rate_limited = _make_rate_limit_error(retry_after=100000)
    fake_create = AsyncMock(side_effect=[rate_limited, _ok_response("ok")])
    client.client.messages.create = fake_create

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", new=sleep_mock):
        await client.generate_json("sys", "user")

    slept = sleep_mock.await_args_list[0].args[0]
    assert slept == pytest.approx(_llm_mod._RATE_LIMIT_BACKOFF_CAP_SECONDS)


@pytest.mark.asyncio
async def test_429_uses_exponential_backoff_when_no_retry_after():
    client = AnthropicClient(api_key="k", model="m")
    # Two 429s (no header) then success — exercises retry indices 0 then 1.
    fake_create = AsyncMock(
        side_effect=[
            _make_rate_limit_error(),
            _make_rate_limit_error(),
            _ok_response("ok"),
        ]
    )
    client.client.messages.create = fake_create

    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", new=sleep_mock):
        result = await client.generate_json("sys", "user")

    assert result == "ok"
    base = _llm_mod._RATE_LIMIT_BACKOFF_BASE_SECONDS
    first = sleep_mock.await_args_list[0].args[0]
    second = sleep_mock.await_args_list[1].args[0]
    assert first == pytest.approx(base)
    assert second == pytest.approx(base * 2)


@pytest.mark.asyncio
async def test_propagates_429_after_retries_exhausted():
    client = AnthropicClient(api_key="k", model="m")
    n = _llm_mod._MAX_RATE_LIMIT_RETRIES
    fake_create = AsyncMock(side_effect=[_make_rate_limit_error() for _ in range(n + 1)])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.RateLimitError):
            await client.generate_json("sys", "user")

    # initial attempt + n retries
    assert fake_create.call_count == n + 1


@pytest.mark.asyncio
async def test_structured_retries_on_429_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    tool = {"name": "record", "input_schema": {"type": "object"}}
    ok = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record", input={"a": 1})],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    fake_create = AsyncMock(side_effect=[_make_rate_limit_error(retry_after=1), ok])
    client.client.messages.create = fake_create

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_structured(system="s", user="u", tool=tool)

    assert result == {"a": 1}
    assert fake_create.call_count == 2


@pytest.mark.asyncio
async def test_generate_coach_message_retries_on_429_then_succeeds():
    client = AnthropicClient(api_key="k", model="m")
    ok = _make_ok_message_result()
    err = _make_rate_limit_error(retry_after=1)
    client.client.messages.stream = _make_streaming_ctx([err, ok])

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_coach_message(system="sys", user="user", tools=[])

    assert result.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# stream_chat_turn — 429 resilience on the CHAT streaming path (#625)
#
# The chat path streams tokens (an async generator), so it was left out of #603's
# scope. A 429 is admission-time capacity raised before the first token, so a
# bounded retry re-issues the request with no duplicate output; once tokens have
# streamed it must NOT re-run (that would duplicate the buffered reply).
# ---------------------------------------------------------------------------


def _make_chat_streaming_ctx(outcomes):
    """`messages.stream` factory shaped for stream_chat_turn. Each entry is
    consumed per call: a BaseException raised on context entry (the admission-time
    429), or a (deltas, final) tuple whose text_stream yields `deltas` then whose
    get_final_message returns `final`."""
    calls = iter(outcomes)

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        outcome = next(calls)
        if isinstance(outcome, BaseException):
            raise outcome
        deltas, final = outcome

        async def _text_stream():
            for d in deltas:
                yield d

        stream = MagicMock()
        stream.text_stream = _text_stream()
        stream.get_final_message = AsyncMock(return_value=final)
        yield stream

    return _ctx


@pytest.mark.asyncio
async def test_stream_chat_turn_retries_on_429_then_succeeds():
    """A 429 on the first admission is retried; the second attempt streams the
    reply, so the runner gets a brief wait instead of a failed turn."""
    client = AnthropicClient(api_key="k", model="m")
    err = _make_rate_limit_error(retry_after=1)
    final = _make_ok_message_result()
    client.client.messages.stream = _make_chat_streaming_ctx(
        [err, (["Hello ", "there"], final)]
    )

    deltas = []
    with patch("asyncio.sleep", new=AsyncMock()):
        async for d in client.stream_chat_turn(
            system="s", messages=[{"role": "user", "content": "hi"}]
        ):
            deltas.append(d)

    text = "".join(d.text for d in deltas if d.text)
    assert text == "Hello there"
    finals = [d for d in deltas if d.final is not None]
    assert finals and finals[-1].final.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_stream_chat_turn_propagates_429_after_retries_exhausted():
    """When the bounded retry is exhausted the 429 propagates, so chat.py's
    safe-degrade path serves a transparent 'busy, try again' message."""
    client = AnthropicClient(api_key="k", model="m")
    n = _llm_mod._MAX_RATE_LIMIT_RETRIES
    client.client.messages.stream = _make_chat_streaming_ctx(
        [_make_rate_limit_error() for _ in range(n + 1)]
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.RateLimitError):
            async for _ in client.stream_chat_turn(
                system="s", messages=[{"role": "user", "content": "hi"}]
            ):
                pass


# ---------------------------------------------------------------------------
# ONE retry policy across all four client methods (#801)
#
# The ladder was written four times, byte-identical apart from its log event,
# and the streaming copy had drifted: it carried the 429 rung only, so a
# connection drop or a 5xx on a chat turn degraded on the FIRST failure while
# the identical failure on a report was retried. `RetryLadder` is now the one
# policy; these pin the rungs the streaming path gained, and the guard that
# keeps it from re-issuing a request whose tokens have already been sent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_chat_turn_retries_transient_drop_before_first_token():
    """A connection drop at admission is a transport blip, not an answer. The
    shared ladder retries it exactly as the report path does."""
    client = AnthropicClient(api_key="k", model="m")
    final = _make_ok_message_result()
    client.client.messages.stream = _make_chat_streaming_ctx(
        [anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
         (["Hi"], final)]
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        deltas = [
            d async for d in client.stream_chat_turn(
                system="s", messages=[{"role": "user", "content": "hi"}]
            )
        ]

    assert "".join(d.text for d in deltas if d.text) == "Hi"


@pytest.mark.asyncio
async def test_stream_chat_turn_retries_5xx_before_first_token():
    client = AnthropicClient(api_key="k", model="m")
    final = _make_ok_message_result()
    client.client.messages.stream = _make_chat_streaming_ctx(
        [_make_status_error(503), (["Hi"], final)]
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        deltas = [
            d async for d in client.stream_chat_turn(
                system="s", messages=[{"role": "user", "content": "hi"}]
            )
        ]

    assert "".join(d.text for d in deltas if d.text) == "Hi"


@pytest.mark.asyncio
async def test_stream_chat_turn_does_not_retry_4xx():
    """A 4xx is a caller bug and will not get better; it propagates at once."""
    client = AnthropicClient(api_key="k", model="m")
    client.client.messages.stream = _make_chat_streaming_ctx([_make_status_error(400)])

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(anthropic.APIStatusError):
            async for _ in client.stream_chat_turn(
                system="s", messages=[{"role": "user", "content": "hi"}]
            ):
                pass


@pytest.mark.parametrize(
    "raised",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
        _make_status_error(503),
        _make_rate_limit_error(retry_after=1),
    ],
    ids=["transient", "5xx", "429"],
)
@pytest.mark.asyncio
async def test_stream_chat_turn_never_reissues_once_a_token_has_streamed(raised):
    """The load-bearing streaming guard: a failure AFTER the first token must
    propagate, because the caller has already buffered output and a re-run would
    duplicate the reply. Parameterised over EVERY rung, since the guard is the
    reason sharing the ladder with the single-shot paths is safe at all — each of
    these would otherwise have re-issued."""
    client = AnthropicClient(api_key="k", model="m")
    calls = {"n": 0}

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        calls["n"] += 1

        async def _text_stream():
            yield "partial"
            raise raised

        stream = MagicMock()
        stream.text_stream = _text_stream()
        stream.get_final_message = AsyncMock(return_value=_make_ok_message_result())
        yield stream

    client.client.messages.stream = _ctx

    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(type(raised)):
            async for _ in client.stream_chat_turn(
                system="s", messages=[{"role": "user", "content": "hi"}]
            ):
                pass

    assert calls["n"] == 1  # never re-issued


@pytest.mark.asyncio
async def test_stream_chat_turn_reports_cache_token_buckets():
    """#786: the chat prefix is cached, so the cache buckets must ride the
    result. Without them the per-user budget counter under-reports every
    conversational turn by the whole cached prefix."""
    client = AnthropicClient(api_key="k", model="m")
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=22,
            cache_read_input_tokens=3333,
            cache_creation_input_tokens=44,
        ),
    )
    client.client.messages.stream = _make_chat_streaming_ctx([(["hi"], final)])

    deltas = [
        d async for d in client.stream_chat_turn(
            system="s", messages=[{"role": "user", "content": "hi"}]
        )
    ]
    result = [d for d in deltas if d.final is not None][-1].final
    assert result.input_tokens == 11
    assert result.output_tokens == 22
    assert result.cache_read_input_tokens == 3333
    assert result.cache_creation_input_tokens == 44
