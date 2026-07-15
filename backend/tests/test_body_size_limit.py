"""#705: the edge request-body-size limit middleware.

Driven at the ASGI level (no app/auth needed) via asyncio.run, so the test is
robust regardless of the pytest-asyncio mode. Covers the declared-length fast
reject, the streamed/chunked bound, small-body passthrough, and that non-HTTP
scopes (websocket/lifespan) pass straight through.
"""

import asyncio

from app.core.body_size_limit import BodySizeLimitMiddleware

CAP = 100


async def _capturing_app(scope, receive, send):
    """A minimal ASGI app that drains the body and 200s, recording what it saw."""
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            scope["_disconnected"] = True
            break
        if message["type"] == "http.request":
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
    scope["_body_len"] = len(body)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(headers):
    return {"type": "http", "headers": headers, "_disconnected": False, "_body_len": 0}


async def _run(mw, scope, incoming):
    """Drive one request; `incoming` is the list of ASGI receive messages."""
    it = iter(incoming)

    async def receive():
        return next(it)

    sent = []

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    return sent


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def test_declared_length_over_cap_is_rejected_before_body_read():
    mw = BodySizeLimitMiddleware(_capturing_app, max_body_bytes=CAP)
    scope = _http_scope([(b"content-length", str(CAP + 1).encode())])
    # A single receive that would blow the cap; it must never be consumed.
    sent = asyncio.run(_run(mw, scope, [{"type": "http.request", "body": b"x" * (CAP + 1)}]))
    assert _status(sent) == 413
    assert scope["_body_len"] == 0  # app never ran on the oversize body


def test_declared_length_at_cap_passes_through():
    mw = BodySizeLimitMiddleware(_capturing_app, max_body_bytes=CAP)
    scope = _http_scope([(b"content-length", str(CAP).encode())])
    sent = asyncio.run(_run(mw, scope, [{"type": "http.request", "body": b"x" * CAP}]))
    assert _status(sent) == 200
    assert scope["_body_len"] == CAP


def test_streamed_body_over_cap_is_cut_off():
    # No content-length (chunked): the middleware counts and disconnects once the
    # accumulated body crosses the cap, bounding the buffer.
    mw = BodySizeLimitMiddleware(_capturing_app, max_body_bytes=CAP)
    scope = _http_scope([])
    chunks = [
        {"type": "http.request", "body": b"a" * 60, "more_body": True},
        {"type": "http.request", "body": b"b" * 60, "more_body": True},  # now 120 > 100
        {"type": "http.request", "body": b"c" * 60, "more_body": False},
    ]
    sent = asyncio.run(_run(mw, scope, chunks))
    # The app saw a disconnect instead of the full 180-byte body.
    assert scope["_disconnected"] is True
    assert scope["_body_len"] < 180


def test_non_http_scope_passes_through_untouched():
    seen = {}

    async def ws_app(scope, receive, send):
        seen["type"] = scope["type"]

    mw = BodySizeLimitMiddleware(ws_app, max_body_bytes=CAP)

    async def receive():
        return {"type": "websocket.receive"}

    async def send(message):
        pass

    asyncio.run(mw({"type": "websocket"}, receive, send))
    assert seen["type"] == "websocket"
