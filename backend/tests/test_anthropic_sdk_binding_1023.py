"""#1023: bind `llm.py`'s call sites to the REAL `anthropic` SDK.

Every other coach test replaces `client.messages` with a mock, so nothing in
the suite ever calls the SDK's own `create()` / `stream()`. That is how #966
shipped: 1.0.0 removed `temperature` from both signatures, the three call sites
passing it raised TypeError on every real call, and the suite stayed green.

Here the SDK is real end to end and only the network is faked, via an
`httpx2.MockTransport` handed to `AsyncAnthropic`. So a keyword the SDK no longer
accepts fails in this file, the request body we actually send is what gets
asserted, and a disconnect mid-body is raised by the SDK's own stream iterator,
which is the only way to prove `RetryLadder` still recognises it.
"""

import json
from unittest.mock import AsyncMock, patch

import anthropic
import httpx2
import pytest

from app.services.coach.llm import AnthropicClient

_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 11, "output_tokens": 7},
}

_TOOL = {
    "name": "emit",
    "description": "emit",
    "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
}


def _sse(text: str = "hi") -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {**_MESSAGE, "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class _DiesMidBody(httpx2.AsyncByteStream):
    """Sends the opening event, then the peer closes before the body completes."""

    async def __aiter__(self):
        yield _sse().split(b"\n\n")[0] + b"\n\n"
        raise httpx2.RemoteProtocolError("peer closed connection without sending complete message body")


def _client(responses):
    """An AnthropicClient on the real SDK; `responses` is consumed one per request."""
    sent = []
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return queue.pop(0)

    client = AnthropicClient(api_key="test-key", model="claude-sonnet-4-6")
    client.client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,  # the app's RetryLadder is what is under test, not the SDK's
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return client, sent


def _json(body: dict) -> httpx2.Response:
    return httpx2.Response(200, json=body)


def _stream(body) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body) \
        if isinstance(body, bytes) else \
        httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=body)


@pytest.mark.asyncio
async def test_generate_json_binds_and_keeps_its_temperature():
    client, sent = _client([_json({**_MESSAGE, "content": [{"type": "text", "text": "{}"}]})])

    text, usage = await client.generate_json_with_usage(system="s", user="u", max_tokens=64)

    assert text == "{}" and usage.input_tokens == 11
    assert sent[0]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_generate_structured_binds_and_keeps_its_temperature():
    client, sent = _client([_json({
        **_MESSAGE,
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": "emit", "input": {"x": 1}}],
    })])

    result, _usage = await client.generate_structured_with_usage(
        system="s", user="u", tool=_TOOL, max_tokens=64
    )

    assert result == {"x": 1}
    assert sent[0]["temperature"] == 0
    assert sent[0]["tool_choice"] == {"type": "tool", "name": "emit"}


@pytest.mark.asyncio
async def test_generate_coach_message_binds_and_sends_no_sampling_params():
    client, sent = _client([_stream(_sse("report"))])

    result = await client.generate_coach_message(system="s", user="u", tools=[_TOOL])

    assert result.content_blocks[0].text == "report"
    # Built to the Opus parameter surface on purpose: no sampling params at all.
    assert not {"temperature", "top_p", "top_k"} & sent[0].keys()
    assert sent[0]["thinking"] == {"type": "adaptive"}


@pytest.mark.asyncio
async def test_stream_chat_turn_binds_and_keeps_its_temperature():
    client, sent = _client([_stream(_sse("reply"))])

    deltas = [d async for d in client.stream_chat_turn(
        system="s", messages=[{"role": "user", "content": "u"}], tools=[_TOOL]
    )]

    assert [d.text for d in deltas if d.text] == ["reply"]
    assert deltas[-1].final.stop_reason == "end_turn"
    assert sent[0]["temperature"] == 0.3


@pytest.mark.asyncio
async def test_a_disconnect_mid_body_is_retried_by_the_ladder():
    """#302 under the SDK's real transport: the error the SDK's stream iterator
    lets escape must be one `RetryLadder` classifies as transient."""
    client, sent = _client([_stream(_DiesMidBody()), _stream(_sse("second try"))])

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await client.generate_coach_message(system="s", user="u", tools=[])

    assert result.content_blocks[0].text == "second try"
    assert len(sent) == 2


# --- generate_structured_reasoned (#1064) ----------------------------------
#
# The thinking, web-searching structured call, driven through the real SDK like
# the rest of this file: the request body we send is what is asserted, and the
# SDK's own stream accumulator assembles the response we read.


def _reasoned_sse(blocks, *, stop_reason="tool_use", output_tokens=40, searches=0):
    """An SSE body for `blocks`, each `(content_block_start payload, [delta payloads])`."""
    events = [("message_start", {"type": "message_start", "message": {
        **_MESSAGE, "content": [], "stop_reason": None,
        "usage": {"input_tokens": 100, "output_tokens": 1},
    }})]
    for i, (start, deltas) in enumerate(blocks):
        events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}))
        for d in deltas:
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": d}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    usage = {"output_tokens": output_tokens}
    if searches:
        usage["server_tool_use"] = {"web_search_requests": searches}
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": usage}))
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def _tool_use_block(name="emit", payload=None, block_id="toolu_1"):
    return (
        {"type": "tool_use", "id": block_id, "name": name, "input": {}},
        [{"type": "input_json_delta", "partial_json": json.dumps(payload if payload is not None else {"x": 1})}],
    )


def _reasoned_client(responses, model):
    client, sent = _client(responses)
    client.model = model
    return client, sent


@pytest.mark.asyncio
async def test_reasoned_call_on_a_5_5_model_thinks_searches_and_never_forces_the_tool():
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block(payload={"x": 7})]))], "claude-opus-5-5"
    )

    result, _usage = await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=1000,
        effort="high", web_search_max_uses=2,
    )

    body = sent[0]
    assert result == {"x": 7}
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert body["tool_choice"] == {"type": "auto"}
    assert [t["name"] for t in body["tools"]] == ["emit", "web_search"]
    assert body["tools"][1]["max_uses"] == 2
    # The tool is asked for in the user turn instead, and no sampling params travel.
    assert body["messages"][0]["content"].startswith("u")
    assert "`emit`" in body["messages"][0]["content"]
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    # The answer's own budget plus room to think, never past the output ceiling.
    assert body["max_tokens"] == 1000 + 32_000


@pytest.mark.asyncio
async def test_reasoned_call_caps_its_total_at_the_output_ceiling():
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-opus-5-5"
    )

    await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=120_000
    )

    assert sent[0]["max_tokens"] == 128_000


@pytest.mark.asyncio
async def test_reasoned_call_without_search_offers_only_the_answer_tool():
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-sonnet-5-5"
    )

    await client.generate_structured_reasoned(system="s", user="u", tool=_TOOL, max_tokens=64)

    assert [t["name"] for t in sent[0]["tools"]] == ["emit"]


@pytest.mark.asyncio
async def test_reasoned_call_offers_no_search_when_the_kill_switch_is_off(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "COACH_EVENT_SEARCH_ENABLED", False)
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-opus-5-5"
    )

    await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=64, web_search_max_uses=3
    )

    assert [t["name"] for t in sent[0]["tools"]] == ["emit"]


@pytest.mark.asyncio
async def test_reasoned_call_on_a_4x_model_is_the_forced_thinking_free_call():
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-sonnet-4-6"
    )

    await client.generate_structured_reasoned(system="s", user="u", tool=_TOOL, max_tokens=64)

    body = sent[0]
    assert "thinking" not in body and "output_config" not in body
    assert body["tool_choice"] == {"type": "tool", "name": "emit"}
    assert body["temperature"] == 0
    assert body["max_tokens"] == 64
    assert body["messages"][0]["content"] == "u"


@pytest.mark.asyncio
async def test_reasoned_call_on_a_4x_model_that_searches_cannot_force_the_tool():
    """Forcing the answer tool would stop the model ever reaching for the search."""
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-sonnet-4-6"
    )

    await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=64, web_search_max_uses=2
    )

    body = sent[0]
    assert body["tool_choice"] == {"type": "auto"}
    assert [t["name"] for t in body["tools"]] == ["emit", "web_search"]
    assert "`emit`" in body["messages"][0]["content"]
    assert "thinking" not in body


@pytest.mark.asyncio
async def test_reasoned_call_returns_the_named_tools_input_ignoring_everything_else():
    thinking = (
        {"type": "thinking", "thinking": "", "signature": ""},
        [{"type": "thinking_delta", "thinking": "let me think"}, {"type": "signature_delta", "signature": "sig"}],
    )
    prose = ({"type": "text", "text": ""}, [{"type": "text_delta", "text": "here you go"}])
    client, _sent = _reasoned_client(
        [_stream(_reasoned_sse([
            thinking, prose,
            _tool_use_block(name="other", payload={"x": 99}, block_id="toolu_0"),
            _tool_use_block(name="emit", payload={"x": 3}, block_id="toolu_1"),
        ]))],
        "claude-opus-5-5",
    )

    result, _usage = await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=64
    )

    assert result == {"x": 3}


@pytest.mark.asyncio
async def test_reasoned_call_raises_when_the_tool_was_never_called():
    prose = ({"type": "text", "text": ""}, [{"type": "text_delta", "text": "no tool"}])
    client, _sent = _reasoned_client(
        [_stream(_reasoned_sse([prose], stop_reason="end_turn"))], "claude-opus-5-5"
    )

    with pytest.raises(ValueError, match="no emit tool_use block"):
        await client.generate_structured_reasoned(system="s", user="u", tool=_TOOL, max_tokens=64)


@pytest.mark.asyncio
async def test_reasoned_call_raises_on_a_max_tokens_stop():
    """A truncated tool call arrives as a block whose input is `{}`, which is
    indistinguishable from a real empty answer unless the stop reason is read (#931)."""
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block(payload={})], stop_reason="max_tokens"))],
        "claude-opus-5-5",
    )

    with pytest.raises(ValueError, match="truncated at max_tokens"):
        await client.generate_structured_reasoned(system="s", user="u", tool=_TOOL, max_tokens=64)
    assert len(sent) == 1  # not retried: the same call at the same cap truncates again


def _paused_turn_blocks():
    return [
        (
            {"type": "thinking", "thinking": "", "signature": ""},
            [{"type": "thinking_delta", "thinking": "need a date"}, {"type": "signature_delta", "signature": "sig-1"}],
        ),
        (
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}},
            [{"type": "input_json_delta", "partial_json": json.dumps({"query": "kent half marathon"})}],
        ),
        (
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                {"type": "web_search_result", "url": "https://e.example", "title": "T",
                 "encrypted_content": "enc", "page_age": None},
            ]},
            [],
        ),
    ]


@pytest.mark.asyncio
async def test_a_paused_turn_is_continued_with_the_paused_message_echoed_back():
    client, sent = _reasoned_client(
        [
            _stream(_reasoned_sse(_paused_turn_blocks(), stop_reason="pause_turn", output_tokens=30, searches=1)),
            _stream(_reasoned_sse([_tool_use_block(payload={"x": 5})], output_tokens=20, searches=1)),
        ],
        "claude-opus-5-5",
    )

    result, usage = await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=64, web_search_max_uses=2
    )

    assert result == {"x": 5}
    assert len(sent) == 2
    echoed = sent[1]["messages"][1]
    assert echoed["role"] == "assistant"
    kinds = [b["type"] for b in echoed["content"]]
    assert kinds == ["thinking", "server_tool_use", "web_search_tool_result"]
    assert echoed["content"][0]["signature"] == "sig-1"
    assert echoed["content"][2]["content"][0]["encrypted_content"] == "enc"
    # Both rounds are summed, searches included.
    assert usage.output_tokens == 50
    assert usage.input_tokens == 200
    assert usage.web_search_requests == 2


@pytest.mark.asyncio
async def test_pause_continuations_are_bounded():
    paused = lambda: _stream(_reasoned_sse(_paused_turn_blocks(), stop_reason="pause_turn"))  # noqa: E731
    client, sent = _reasoned_client([paused() for _ in range(4)], "claude-opus-5-5")

    with pytest.raises(ValueError, match="no emit tool_use block"):
        await client.generate_structured_reasoned(
            system="s", user="u", tool=_TOOL, max_tokens=64, web_search_max_uses=2
        )

    assert len(sent) == 4  # the first call plus three continuations, then it stops


@pytest.mark.asyncio
async def test_searches_are_carried_on_the_usage_the_budget_prices():
    client, _sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()], searches=2))], "claude-opus-5-5"
    )

    _result, usage = await client.generate_structured_reasoned(
        system="s", user="u", tool=_TOOL, max_tokens=64, web_search_max_uses=2
    )

    assert usage.web_search_requests == 2


@pytest.mark.asyncio
async def test_reasoned_call_retries_a_dropped_stream():
    client, sent = _reasoned_client(
        [_stream(_DiesMidBody()), _stream(_reasoned_sse([_tool_use_block(payload={"x": 2})]))],
        "claude-opus-5-5",
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        result, _usage = await client.generate_structured_reasoned(
            system="s", user="u", tool=_TOOL, max_tokens=64
        )

    assert result == {"x": 2} and len(sent) == 2


@pytest.mark.asyncio
async def test_reasoned_call_honours_its_callers_deadline():
    client, sent = _reasoned_client(
        [_stream(_reasoned_sse([_tool_use_block()]))], "claude-opus-5-5"
    )

    with pytest.raises(TimeoutError):
        await client.generate_structured_reasoned(
            system="s", user="u", tool=_TOOL, max_tokens=64, timeout=0
        )
    assert sent == []  # refused before spending anything


class _Stalls(httpx2.AsyncByteStream):
    """A response that never finishes inside the caller's budget."""

    async def __aiter__(self):
        import asyncio

        await asyncio.sleep(5)
        yield _reasoned_sse([_tool_use_block()])


@pytest.mark.asyncio
async def test_a_stalled_reasoned_call_is_cut_off_at_its_callers_deadline():
    """The SDK's own timeout is per read, so it cannot bound a call that keeps
    trickling; the deadline has to be enforced around the whole round."""
    client, _sent = _reasoned_client([_stream(_Stalls())], "claude-opus-5-5")

    with pytest.raises(TimeoutError):
        await client.generate_structured_reasoned(
            system="s", user="u", tool=_TOOL, max_tokens=64, timeout=0.2
        )
