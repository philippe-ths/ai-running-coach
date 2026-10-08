"""Pins which request shapes each model generation gets (Claude 5.5 migration).

The 5.5 models 400 on a forced tool_choice and on any non-default temperature;
the 4.x models in production need both kept. Source of the constraint:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
"""

import json

import anthropic
import httpx2
import pytest

from app.services.coach import model_capabilities as caps
from app.services.coach.llm import AnthropicClient

LEGACY = ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-opus-4-6"]
FIVE_FIVE = ["claude-opus-5-5", "claude-sonnet-5-5"]


@pytest.mark.parametrize("model", LEGACY)
def test_4x_models_keep_temperature_and_forced_tool_choice(model):
    assert caps.accepts_sampling(model)
    assert caps.accepts_forced_tool_choice(model)
    assert caps.sampling_kwargs(model, 0.2) == {"extra_body": {"temperature": 0.2}}
    assert caps.tool_choice_kwargs(model, "emit") == {
        "tool_choice": {"type": "tool", "name": "emit"}
    }


@pytest.mark.parametrize("model", FIVE_FIVE)
def test_5_5_models_get_no_temperature_and_no_forced_tool_choice(model):
    assert not caps.accepts_sampling(model)
    assert not caps.accepts_forced_tool_choice(model)
    assert caps.sampling_kwargs(model, 0.2) == {}
    assert caps.tool_choice_kwargs(model, "emit") == {"tool_choice": {"type": "auto"}}


@pytest.mark.parametrize("model", ["mystery-model", "", "claude-future-9"])
def test_an_unrecognised_model_gets_the_shape_valid_everywhere(model):
    assert caps.sampling_kwargs(model, 0.2) == {}
    assert caps.tool_choice_kwargs(model, "emit") == {"tool_choice": {"type": "auto"}}


def test_haiku_5_5_still_takes_a_forced_tool_but_no_temperature():
    assert caps.accepts_forced_tool_choice("claude-haiku-5-5")
    assert not caps.accepts_sampling("claude-haiku-5-5")


# --- the request body that actually leaves the real SDK ------------------------

_MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
    "stop_reason": "tool_use", "stop_sequence": None,
    "usage": {"input_tokens": 3, "output_tokens": 2},
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "t1", "name": "emit", "input": {"x": 1}},
    ],
}
_TOOL = {"name": "emit", "description": "d", "input_schema": {"type": "object"}}


def _client(model):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=_MESSAGE)

    c = AnthropicClient(api_key="k", model=model)
    c.client = anthropic.AsyncAnthropic(
        api_key="k", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return c, sent


@pytest.mark.asyncio
async def test_structured_call_on_sonnet_5_5_sends_auto_choice_no_temperature_and_asks_in_prompt():
    c, sent = _client("claude-sonnet-5-5")
    result, _ = await c.generate_structured_with_usage(system="s", user="u", tool=_TOOL, max_tokens=64)

    assert result == {"x": 1}  # the thinking block ahead of the tool_use is skipped
    body = sent[0]
    assert body["tool_choice"] == {"type": "auto"}
    assert "temperature" not in body
    # A quality lane keeps the model's default thinking and effort, with headroom.
    assert "thinking" not in body and "output_config" not in body
    assert body["max_tokens"] == 64 + caps.thinking_headroom("claude-sonnet-5-5") > 64
    assert "`emit`" in body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_structured_call_on_sonnet_4_6_still_forces_the_tool_at_temperature_0():
    c, sent = _client("claude-sonnet-4-6")
    await c.generate_structured_with_usage(system="s", user="u", tool=_TOOL, max_tokens=64)

    body = sent[0]
    assert body["tool_choice"] == {"type": "tool", "name": "emit"}
    assert body["temperature"] == 0
    assert body["messages"][0]["content"] == "u"
    assert "thinking" not in body
    assert body["max_tokens"] == 64  # no headroom where thinking is off by default


def test_chat_thinking_and_headroom_by_model():
    assert caps.chat_thinking_kwargs("claude-sonnet-5-5") == {"thinking": {"type": "between_tools"}}
    assert caps.chat_thinking_kwargs("claude-opus-5-5") == {"output_config": {"effort": "low"}}
    assert caps.chat_thinking_kwargs("claude-sonnet-4-6") == {}
    assert caps.thinking_headroom("claude-opus-5-5") > 0
    assert caps.thinking_headroom("claude-sonnet-4-6") == 0


@pytest.mark.asyncio
async def test_generate_json_raises_on_a_max_tokens_stop():
    def handler(request):
        return httpx2.Response(200, json={
            **_MESSAGE, "stop_reason": "max_tokens",
            "content": [{"type": "text", "text": '{"cut'}],
        })

    c = AnthropicClient(api_key="k", model="claude-sonnet-4-6")
    c.client = anthropic.AsyncAnthropic(
        api_key="k", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(ValueError, match="truncated"):
        await c.generate_json_with_usage(system="s", user="u", max_tokens=64)


@pytest.mark.asyncio
async def test_chat_stream_yields_a_tick_for_non_text_events():
    events = [
        ("message_start", {"type": "message_start", "message": {**_MESSAGE, "content": [], "stop_reason": None}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "s"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    sse = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
    c = AnthropicClient(api_key="k", model="claude-opus-5-5")
    c.client = anthropic.AsyncAnthropic(
        api_key="k", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda r: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse))),
    )
    deltas = [d async for d in c.stream_chat_turn(system="s", messages=[{"role": "user", "content": "u"}])]
    ticks = [d for d in deltas if d.text is None and d.final is None]
    assert len(ticks) >= 2  # a thinking-only stream still gives the caller heartbeat chances
    assert deltas[-1].final is not None


@pytest.mark.parametrize("model", FIVE_FIVE + ["claude-opus-5", "claude-fable-5-1"])
def test_5x_models_support_thinking(model):
    assert caps.supports_thinking(model)


@pytest.mark.parametrize("model", LEGACY + ["mystery-model", ""])
def test_4x_and_unrecognised_models_get_no_thinking(model):
    assert not caps.supports_thinking(model)
