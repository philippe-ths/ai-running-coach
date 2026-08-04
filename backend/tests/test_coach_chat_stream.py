"""Repro for #223: 'Chat with Coach' streaming.

Exercises the POST /api/coach/threads/messages SSE endpoint end to end with
a mocked LLM, then reconstructs the client-visible text from the SSE frames the
way the frontend does. The coach prompt explicitly asks for multi-paragraph
markdown, so the LLM emits text deltas containing newlines; the wire format must
survive them.
"""

import json
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from tests._chat_stubs import chat_raising_stub, chat_turn_stub


def _seed_activity_with_report(db) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=1, access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(
        user_id=user.id, strava_activity_id=42,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Test run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.add(CoachReport(
        activity_id=activity.id, report={"key_takeaways": [{"text": "ok"}]},
        meta={}, context_pack={}, prompt_id="x", schema_version=1, is_fallback=False,
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _seed_activity_without_report(db) -> Activity:
    """An activity with metrics but NO CoachReport — the receipt-cadence window
    where the report is still generating asynchronously (#685)."""
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.add(StravaAccount(user_id=user.id, strava_athlete_id=7, access_token="t", refresh_token="r", expires_at=9999999999, scope="read"))
    activity = Activity(
        user_id=user.id, strava_activity_id=99,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Fresh run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _reconstruct_from_sse(raw: str) -> str:
    """Reconstruct the assistant text from the SSE stream the frontend's way:
    split on the blank-line event delimiter, then JSON-decode each data frame."""
    out = []
    for event in raw.split("\n\n"):
        for line in event.split("\n"):
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                continue
            frame = json.loads(data)
            # Object frames (the thread announcement, status, trace) are not
            # reply text; the runner-visible reply is the string frames.
            if isinstance(frame, str):
                out.append(frame)
    return "".join(out)


def test_chat_stream_preserves_multiline_markdown(client, db):
    activity = _seed_activity_with_report(db)

    # A realistic coach reply: markdown with blank lines and a bullet list,
    # streamed as deltas that straddle newlines (as the SDK actually does).
    reply = "Great run!\n\nHere's what stood out:\n\n- Steady pace\n- Strong finish"

    # The Anthropic SDK yields text deltas of arbitrary size, including ones that
    # contain newlines; the chat path re-streams the validated reply in slices.
    stub = chat_turn_stub(
        ["Great run!\n\nHere's ", "what stood out:\n\n- Steady pace\n", "- Strong finish"]
    )

    with patch("app.services.coach.llm.AnthropicClient.stream_chat_turn", new=stub):
        resp = client.post(
            "/api/coach/threads/messages",
            json={
                "message": "How did I do?",
                "anchor_activity_id": str(activity.id),
            },
        )

    assert resp.status_code == 200
    reconstructed = _reconstruct_from_sse(resp.text)
    assert reconstructed == reply, f"got {reconstructed!r}"
    # The user turn and the assembled assistant turn are both persisted.
    from app.models.coach_chat_message import CoachChatMessage

    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id)
        .order_by(CoachChatMessage.created_at.asc())
        .all()
    )
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].content == reply


def test_chat_works_before_a_report_exists(client, db):
    """#685: chatting an activity that has no CoachReport yet must NOT fail. It
    used to 400 (surfaced as "couldn't reach your coach"); now the stream opens
    200 and the coach answers from the activity's own data + query tools."""
    activity = _seed_activity_without_report(db)
    from app.models.coach_report import CoachReport

    assert (
        db.query(CoachReport).filter(CoachReport.activity_id == activity.id).count() == 0
    )

    reply = "Nice steady effort. Let's talk about it."
    stub = chat_turn_stub(["Nice steady effort. ", "Let's talk about it."])
    with patch("app.services.coach.llm.AnthropicClient.stream_chat_turn", new=stub):
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "How did that run go?", "anchor_activity_id": str(activity.id)},
        )

    assert resp.status_code == 200
    reconstructed = _reconstruct_from_sse(resp.text)
    assert reconstructed == reply, f"got {reconstructed!r}"

    # The turn is persisted like any other, so the conversation continues.
    from app.models.coach_chat_message import CoachChatMessage

    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id)
        .order_by(CoachChatMessage.created_at.asc())
        .all()
    )
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].content == reply


def test_chat_degrades_gracefully_on_exhausted_rate_limit(client, db):
    """#625: when the bounded 429 retry is exhausted, the chat turn degrades to a
    transparent 'busy, try again' message with a 200/intact connection — not a
    crash or the generic error."""
    activity = _seed_activity_with_report(db)

    import anthropic
    import httpx

    def _rate_limited():
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=429, request=req)
        return anthropic.RateLimitError("rate limited", response=resp, body=None)

    async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
        raise _rate_limited()
        yield  # unreachable; makes this an async generator like the real method

    with patch("app.services.coach.llm.AnthropicClient.stream_chat_turn", new=_stub):
        resp = client.post(
            "/api/coach/threads/messages",
            json={
                "message": "How did I do?",
                "anchor_activity_id": str(activity.id),
            },
        )

    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    reconstructed = _reconstruct_from_sse(resp.text).lower()
    assert "requests" in reconstructed and "moment" in reconstructed


def test_chat_stream_reports_error_without_breaking_connection(client, db):
    """A failure mid-stream must not sever the connection (which the browser
    shows as a bare "Load failed") — it streams a readable message and closes
    cleanly with the [DONE] sentinel (#223)."""
    activity = _seed_activity_with_report(db)

    with patch("app.services.coach.llm.AnthropicClient.stream_chat_turn", new=chat_raising_stub()):
        resp = client.post(
            "/api/coach/threads/messages",
            json={
                "message": "How did I do?",
                "anchor_activity_id": str(activity.id),
            },
        )

    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    reconstructed = _reconstruct_from_sse(resp.text)
    assert reconstructed  # a human-readable message, not an empty/severed stream
