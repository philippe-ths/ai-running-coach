"""#375: coach chat keeps the connection alive during the buffer-then-validate gap.

#340 made chat assemble + validate the FULL reply before emitting any content (so
the safety gate can run on the whole text). That replaced continuous token
streaming with a long silent gap after the route's `: ok` keepalive frame, and a
connection/idle timeout in the prod proxy path then severed the stream — the runner
saw "couldn't reach your coach."

The fix keeps the byte-flow alive with content-free SSE heartbeat comment frames
during buffering, without leaking unvalidated content. These tests drive the REAL
streaming path via httpx ASGITransport (which streams incrementally), NOT TestClient
(which buffers the whole body and is blind to this failure mode by construction).
"""

import asyncio
import json

import httpx
import pytest

from app.db.session import get_db
from app.main import app
from app.services.coach import chat as chatmod
from tests.test_chat_policy_validation import _seed


def _override_db(db):
    def _get():
        yield db
    return _get


async def _stream_sse_lines(activity_id, message):
    """POST a chat turn over the real ASGI streaming transport, returning the SSE
    lines in the order they were delivered."""
    transport = httpx.ASGITransport(app=app)
    lines = []
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        async with ac.stream(
            "POST",
            "/api/coach/threads/messages",
            json={"message": message, "anchor_activity_id": str(activity_id)},
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                lines.append(line)
    return lines


def _heartbeat_indices(lines):
    return [i for i, ln in enumerate(lines) if ln.strip() == ": hb"]


def _content_indices(lines):
    """Frames carrying reply text. A thread turn also emits object frames (its
    thread announcement, status, trace) before the reply is buffered; those are
    not content, and counting them would hide the very gap under test."""
    out = []
    for i, ln in enumerate(lines):
        if not ln.startswith("data: ") or ln.strip() == "data: [DONE]":
            continue
        if isinstance(json.loads(ln[len("data: "):]), str):
            out.append(i)
    return out


@pytest.mark.asyncio
async def test_heartbeats_precede_content_during_buffering(db, monkeypatch):
    """A keepalive heartbeat flows during the silent buffering gap, before any
    content frame — restoring the byte-flow cadence token streaming used to give,
    so a proxy idle timeout cannot sever the stream while the reply is assembled."""
    # Shrink the cadence so several heartbeats fire during a short mocked generation.
    # raising=False so the test is a clean behavioural RED before the constant exists.
    monkeypatch.setattr(chatmod, "_HEARTBEAT_INTERVAL_SECONDS", 0.01, raising=False)

    parts = ["Solid ", "easy ", "run ", "today, ", "well ", "controlled."]

    from tests._chat_stubs import chat_turn_stub

    async def _sleep(_delta):
        await asyncio.sleep(0.02)  # simulate generation time between deltas

    monkeypatch.setattr(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        chat_turn_stub(parts, on_delta=_sleep),
    )

    activity = _seed(db)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        lines = await _stream_sse_lines(str(activity.id), "How did I do?")
    finally:
        app.dependency_overrides.pop(get_db, None)

    hb = _heartbeat_indices(lines)
    content = _content_indices(lines)

    assert len(hb) >= 2, f"expected keepalive frames during buffering, got {lines}"
    assert content, "expected content frames"
    # every heartbeat precedes the first content frame: bytes flowed during the gap
    assert max(hb) < min(content)
    # and the content still reconstructs to the full reply (gating path intact)
    text = "".join(json.loads(lines[i][len("data: "):]) for i in content)
    assert text == "".join(parts)
