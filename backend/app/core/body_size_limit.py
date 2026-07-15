"""#705: reject an oversized request body before it is buffered to disk.

FastAPI/Starlette parses the whole multipart body -- spooling a large file part
to a temporary file on disk -- BEFORE the endpoint function runs, so the
coach-material upload's own size check (``file.read(cap + 1)``) fires only after
a multi-GB body has already been received and written to disk. That is the DoS
in #705: an authenticated runner POSTs an arbitrarily large ``file`` part and it
is fully spooled before rejection.

This pure-ASGI middleware bounds the total request body at the app edge, before
any router or the form parser touches it:

  - A declared ``Content-Length`` over the cap is rejected with 413 before the
    app is called and before any body byte is read. This is the realistic
    vector: a large upload declares its length.
  - For a chunked / unknown-length body, the ``receive`` stream is counted as it
    arrives and cut off (a synthetic ``http.disconnect``) once the cap is
    exceeded, so buffering is bounded to ~the cap instead of unbounded.

Scope: HTTP requests only; websocket/lifespan scopes pass through untouched.
Only the request ``receive`` side is wrapped, so streaming responses (the SSE
coach chat) are unaffected.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    """Bound the total request body size at the ASGI edge (#705)."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Cheap reject on a declared oversize length, before reading any body.
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await _reject_413(send, self.max_body_bytes)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    # No declared length (chunked) but the streamed body has
                    # crossed the cap. Signal a client disconnect so the ASGI
                    # body parser stops reading; the request then fails rather
                    # than buffering an unbounded body. Buffering is bounded to
                    # ~one chunk past the cap.
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)


def _declared_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject_413(send: Send, cap: int) -> None:
    body = b'{"detail":"Request body exceeds the %d-byte limit."}' % cap
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
