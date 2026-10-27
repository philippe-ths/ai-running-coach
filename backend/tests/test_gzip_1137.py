"""#1137: API responses are gzip-compressed, without touching the SSE stream or auth.

`GZipMiddleware` sits innermost, so BasicAuth/CORS/body-cap run as before. These
tests pin: a large JSON body is compressed and decodes to the same JSON; a small
one is not; the coach-thread SSE stream is neither compressed nor buffered (the
installed Starlette excludes `text/event-stream`, and each event still leaves the
app as its own body message); and a gated route still 401s first.
"""

import asyncio
import gzip
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.middleware.gzip import GZipMiddleware

from app.core.auth import BasicAuthMiddleware
from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import Activity, StravaAccount, User, UserProfile
from tests._chat_stubs import chat_turn_stub


def _seed_user(db) -> User:
    user = User(email=f"gz-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4,
                       max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=int(uuid4().int % 10**9),
                         access_token="t", refresh_token="r", expires_at=9999999999,
                         scope="read,activity:read_all"))
    db.commit()
    app.dependency_overrides[verify_clerk_session] = lambda: user
    return user


def _seed_activities(db, user, n: int) -> None:
    base = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    for i in range(n):
        db.add(Activity(
            user_id=user.id, strava_activity_id=5_100_000 + i + uuid4().int % 10_000,
            start_date=base - timedelta(days=i), type="Run", name=f"Morning run {i}",
            distance_m=8000 + i, moving_time_s=2400, elapsed_time_s=2450,
            elev_gain_m=30.0, avg_hr=145.0, average_speed_mps=3.3, raw_summary={},
        ))
    db.commit()


def test_middleware_order_gzip_innermost():
    classes = [m.cls for m in app.user_middleware]  # outermost first
    assert classes[-1] is GZipMiddleware
    assert classes.index(BasicAuthMiddleware) < classes.index(GZipMiddleware)


def test_large_json_is_gzipped_and_identical(client, db):
    user = _seed_user(db)
    _seed_activities(db, user, 20)

    plain = client.get("/api/activities", headers={"Accept-Encoding": "identity"})
    zipped = client.get("/api/activities", headers={"Accept-Encoding": "gzip"})

    assert plain.status_code == zipped.status_code == 200
    assert "content-encoding" not in plain.headers
    assert len(plain.content) > 1024
    assert zipped.headers["content-encoding"] == "gzip"
    # httpx decodes transparently; the decoded bytes are the uncompressed body.
    assert zipped.content == plain.content
    assert json.loads(zipped.content) == json.loads(plain.content)


def test_small_response_is_not_gzipped(client):
    resp = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


@pytest.mark.asyncio
async def test_sse_stream_is_not_compressed_or_buffered(client, db):
    """Drive the real ASGI stack directly so every `http.response.body` message
    is visible: compression would buffer events into one gzip stream."""
    _seed_user(db)
    long_reply = ["Steady aerobic work. " * 30, "Keep Thursday easy. " * 30]

    sent: list[dict] = []
    body = json.dumps({"message": "how was it?"}).encode()
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # the client stays connected until the end

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/coach/threads/messages",
        "raw_path": b"/api/coach/threads/messages", "root_path": "", "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"accept-encoding", b"gzip"),
                    (b"content-length", str(len(body)).encode())],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }
    with patch("app.services.coach.llm.AnthropicClient.stream_chat_turn",
               new=chat_turn_stub(long_reply)):
        await app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert start["status"] == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers

    chunks = [m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body")]
    # The connection-establishing comment leaves on its own, before any reply.
    assert chunks[0] == b": ok\n\n"
    assert len(chunks) >= 4
    raw = b"".join(chunks)
    assert sum(len(c) for c in chunks) > 1024
    assert b"Steady aerobic work." in raw  # plain text on the wire, not gzip
    with pytest.raises(OSError):
        gzip.decompress(raw)


def test_gated_route_still_401s_before_compression(client, db, monkeypatch):
    monkeypatch.setattr(settings, "BASIC_AUTH_USER", "svc")
    monkeypatch.setattr(settings, "BASIC_AUTH_PASSWORD", "secret")
    resp = client.get("/api/activities", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").lower().startswith("basic")
