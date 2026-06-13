"""Repro for #223: 'Chat with Coach' streaming.

Exercises the POST /api/activities/{id}/coach-chat SSE endpoint end to end with
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
            out.append(json.loads(data))
    return "".join(out)


def test_chat_stream_preserves_multiline_markdown(client, db):
    activity = _seed_activity_with_report(db)

    # A realistic coach reply: markdown with blank lines and a bullet list,
    # streamed as deltas that straddle newlines (as the SDK actually does).
    reply = "Great run!\n\nHere's what stood out:\n\n- Steady pace\n- Strong finish"

    async def fake_stream(self, system, messages, max_tokens=1024):
        # The Anthropic SDK yields text deltas of arbitrary size, including ones
        # that contain newlines.
        for delta in ["Great run!\n\nHere's ", "what stood out:\n\n- Steady pace\n", "- Strong finish"]:
            yield delta

    with patch("app.services.coach.llm.AnthropicClient.stream_chat", new=fake_stream):
        resp = client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
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


def test_chat_stream_reports_error_without_breaking_connection(client, db):
    """A failure mid-stream must not sever the connection (which the browser
    shows as a bare "Load failed") — it streams a readable message and closes
    cleanly with the [DONE] sentinel (#223)."""
    activity = _seed_activity_with_report(db)

    async def boom(self, system, messages, max_tokens=1024):
        raise RuntimeError("upstream exploded")
        yield  # pragma: no cover — makes this an async generator

    with patch("app.services.coach.llm.AnthropicClient.stream_chat", new=boom):
        resp = client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
        )

    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    reconstructed = _reconstruct_from_sse(resp.text)
    assert reconstructed  # a human-readable message, not an empty/severed stream
