"""Shared stubs for the coach-chat streaming path (#648).

The chat path now drives a bounded agentic tool loop through
`AnthropicClient.stream_chat_turn` (not the old single `stream_chat` call), so the
chat tests patch that method with these stubs. Each stub is an async generator with
the real method's signature: it yields `ChatTurnDelta(text=...)` deltas for the
heartbeat cadence, then a terminal `ChatTurnDelta(final=MessageResult(...))`.
"""

from typing import List, Optional


def chat_turn_stub(deltas: List[str], *, capture: Optional[dict] = None, on_delta=None):
    """A `stream_chat_turn` replacement that answers in ONE text round (no tool call).

    Emits each piece of `deltas` as a streamed text delta, then a final text message
    (stop_reason end_turn) whose text is the concatenation — the shape a chat turn
    that needs no data lookup takes. `capture` (a dict) records the `system` prompt and
    the `tools` seen per call; `on_delta` is an optional async hook per delta (e.g. to
    sleep, for the keepalive-timing test).
    """
    from app.services.coach.llm import ChatTurnDelta, MessageResult

    async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
        if capture is not None:
            capture["system"] = system
            capture.setdefault("tools_seen", []).append(tools)
        for d in deltas:
            if on_delta is not None:
                await on_delta(d)
            yield ChatTurnDelta(text=d)
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[{"type": "text", "text": "".join(deltas)}],
                stop_reason="end_turn",
            )
        )

    return _stub


def chat_raising_stub(*, before: str = ""):
    """A `stream_chat_turn` replacement that yields an optional delta then raises,
    to exercise the mid-stream failure -> safe-degrade path."""
    from app.services.coach.llm import ChatTurnDelta

    async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
        if before:
            yield ChatTurnDelta(text=before)
        raise RuntimeError("upstream blew up")

    return _stub


def chat_tool_loop_stub(rounds, *, capture: Optional[dict] = None):
    """A `stream_chat_turn` replacement for the multi-round tool loop (#648).

    `rounds` is consumed one per call. Each entry is either:
      - a str: a final text answer (stop_reason end_turn) that ends the loop, or
      - a list of tool-call specs [{"name":.., "input":{..}, "id":..}, ...]: a
        tool_use turn (stop_reason tool_use) that drives another round.

    `capture` records, per call, the `tools` argument (so a test can assert the final
    round ran tools-off) and the `messages` array (so a test can assert the tool_result
    was fed back). Raises if the loop asks for more rounds than were provided.
    """
    from app.services.coach.llm import ChatTurnDelta, MessageResult

    state = {"i": 0}

    async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
        i = state["i"]
        state["i"] += 1
        if capture is not None:
            capture.setdefault("tools_seen", []).append(tools)
            capture.setdefault("messages_seen", []).append([dict(m) for m in messages])
        if i >= len(rounds):
            raise AssertionError(
                f"stream_chat_turn called {i + 1} times; only {len(rounds)} rounds stubbed"
            )
        spec = rounds[i]
        if isinstance(spec, str):
            yield ChatTurnDelta(text=spec)
            yield ChatTurnDelta(
                final=MessageResult(
                    content_blocks=[{"type": "text", "text": spec}],
                    stop_reason="end_turn",
                )
            )
            return
        blocks = []
        for j, tc in enumerate(spec):
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", f"tu_{i}_{j}"),
                "name": tc["name"],
                "input": tc.get("input", {}),
            })
        yield ChatTurnDelta(
            final=MessageResult(content_blocks=blocks, stop_reason="tool_use")
        )

    _stub.state = state
    return _stub
