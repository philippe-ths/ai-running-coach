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
