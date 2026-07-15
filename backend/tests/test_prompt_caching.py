"""#629: the coach system prompt is passed as a cacheable content block, and the
prompt-cache token counts are captured for verification.

Behaviour-preserving change: these assert the request SHAPE (a single ephemeral
cache_control block over the deterministic system prompt) and the usage capture,
not model output. Driven via asyncio.run so no pytest-asyncio mode is assumed.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.coach.llm import AnthropicClient, _cacheable_system

_EPHEMERAL = {"type": "ephemeral"}


def _client():
    # Constructs a real AsyncAnthropic (no network at construction); the message
    # methods are then overridden with mocks, matching the existing test pattern.
    return AnthropicClient(api_key="test-key-not-real", model="claude-sonnet-4-6")


def test_cacheable_system_wraps_string_with_ephemeral_breakpoint():
    assert _cacheable_system("SYSTEM PROMPT") == [
        {"type": "text", "text": "SYSTEM PROMPT", "cache_control": _EPHEMERAL}
    ]


def test_generate_json_passes_cacheable_system_and_captures_cache_tokens():
    client = _client()
    resp = SimpleNamespace(
        content=[SimpleNamespace(text="{}")],
        usage=SimpleNamespace(
            input_tokens=5,
            output_tokens=2,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=0,
        ),
    )
    fake = AsyncMock(return_value=resp)
    client.client.messages.create = fake
    text, usage = asyncio.run(client.generate_json_with_usage(system="SYS", user="U"))
    assert fake.call_args.kwargs["system"] == [
        {"type": "text", "text": "SYS", "cache_control": _EPHEMERAL}
    ]
    assert usage.cache_read_input_tokens == 10
    assert usage.cache_creation_input_tokens == 0


def test_generate_structured_passes_cacheable_system_and_captures_cache_tokens():
    client = _client()
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record", input={"ok": 1})],
        usage=SimpleNamespace(
            input_tokens=5,
            output_tokens=2,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=10,
        ),
    )
    fake = AsyncMock(return_value=resp)
    client.client.messages.create = fake
    result, usage = asyncio.run(
        client.generate_structured_with_usage(
            system="SYS", user="U", tool={"name": "record"}
        )
    )
    assert fake.call_args.kwargs["system"][0]["cache_control"] == _EPHEMERAL
    assert result == {"ok": 1}
    assert usage.cache_creation_input_tokens == 10


def test_generate_coach_message_caches_system_and_captures_cache_tokens():
    client = _client()
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Nice run.")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=4406,
            output_tokens=1469,
            cache_read_input_tokens=10223,
            cache_creation_input_tokens=0,
        ),
    )
    captured = {}

    @asynccontextmanager
    async def fake_stream(**kwargs):
        captured.update(kwargs)
        stream = MagicMock()
        stream.get_final_message = AsyncMock(return_value=final)
        yield stream

    client.client.messages.stream = fake_stream
    result = asyncio.run(
        client.generate_coach_message(system="SYS", user="U", tools=[])
    )
    assert captured["system"] == [
        {"type": "text", "text": "SYS", "cache_control": _EPHEMERAL}
    ]
    assert result.cache_read_input_tokens == 10223
    assert result.cache_creation_input_tokens == 0
    # Behaviour-preserving: the parsed output is unchanged.
    assert result.stop_reason == "end_turn"
