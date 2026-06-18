"""#340: the deterministic policy validator runs over streamed coach chat output.

Chat is the one place coach prose reached the runner UNVALIDATED — the report path
validates before storing, retries, and forces a fallback on surviving medical
overreach, but `stream_chat_response` streamed raw LLM tokens straight through.

These tests pin the chat-side severity model (which mirrors the report path):
- medical overreach is the hard floor — the raw turn is withheld and replaced with
  a safe, non-diagnostic redirect (the chat analogue of the report's forced
  fallback), and the redirect is what gets streamed AND persisted.
- soft violations (uncalibrated-zone language, ungated interval claims) are logged
  and let through, exactly as the report path tolerates a complete message that
  trips a non-medical rule.

They drive the real HTTP endpoint with a mocked LLM stream, so they exercise the
buffer -> validate -> re-stream path end to end (fork (a)).
"""

import json
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.services.coach.chat import MEDICAL_REDIRECT_MESSAGE, _validate_chat_text
from app.services.coach.validator import check_medical_overreach
from tests.test_policy_validator import _make_pack


def _seed(db, *, context_pack=None) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=1, access_token="t", refresh_token="r",
        expires_at=9999999999, scope="read",
    ))
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
        meta={}, context_pack=context_pack or {}, prompt_id="x", schema_version=1,
        is_fallback=False,
    ))
    db.commit()
    db.refresh(activity)
    return activity


def _post_chat_with_chunks(client, activity, chunks):
    """POST a chat turn with the LLM stream mocked to emit `chunks` verbatim."""

    async def fake_stream(self, system, messages, max_tokens=1024):
        for chunk in chunks:
            yield chunk

    with patch("app.services.coach.llm.AnthropicClient.stream_chat", new=fake_stream):
        return client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
        )


def _post_chat_with_reply(client, activity, reply_text: str):
    """POST a chat turn whose reply is emitted as two chunks (assemble-then-validate)."""
    mid = len(reply_text) // 2
    return _post_chat_with_chunks(client, activity, [reply_text[:mid], reply_text[mid:]])


def _post_chat_raising(client, activity):
    """POST a chat turn whose LLM stream raises partway through."""

    async def fake_stream(self, system, messages, max_tokens=1024):
        yield "Let me think..."
        raise RuntimeError("upstream blew up")

    with patch("app.services.coach.llm.AnthropicClient.stream_chat", new=fake_stream):
        return client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
        )


def _streamed_text(resp) -> str:
    """Reconstruct the reply the runner actually sees from the SSE frames.

    The route emits a `: ok` keepalive comment, then one JSON-encoded `data:` frame
    per slice, then a `data: [DONE]` sentinel. Re-streaming slices the validated
    reply, so the client reconstructs it by JSON-decoding and concatenating frames —
    we mirror that here so assertions are about the reply, not the slice boundaries."""
    out = []
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            continue
        out.append(json.loads(payload))
    return "".join(out)


def _assistant_text(db, activity) -> str:
    msg = (
        db.query(CoachChatMessage)
        .filter(
            CoachChatMessage.activity_id == activity.id,
            CoachChatMessage.role == "assistant",
        )
        .order_by(CoachChatMessage.created_at.desc())
        .first()
    )
    return msg.content if msg else ""


def test_medical_overreach_is_gated_and_replaced(client, db):
    """A chat reply that trips the medical-scope rule is withheld: the runner sees
    the safe redirect, the raw medical text never streams, and the persisted turn
    is the redirect (not the raw)."""
    bad = "Honestly this looks like a stress fracture. Take 200mg of ibuprofen before your next run."
    resp = _post_chat_with_reply(client, _seed(db), bad)
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    # the dangerous text never reaches the wire
    assert "stress fracture" not in resp.text
    assert "200mg" not in resp.text
    # the safe redirect is what the runner sees and what we persist
    assert _streamed_text(resp) == MEDICAL_REDIRECT_MESSAGE
    assert _assistant_text(db, activity) == MEDICAL_REDIRECT_MESSAGE


def test_clean_reply_streams_through_unchanged(client, db):
    """A clean reply is not touched: the runner sees it verbatim and it persists
    verbatim (no-regression on the happy path)."""
    clean = "Solid easy run. Your pace held steady and HR stayed controlled throughout."
    resp = _post_chat_with_reply(client, _seed(db), clean)
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    assert _streamed_text(resp) == clean
    assert _assistant_text(db, activity) == clean


def test_soft_violation_is_logged_but_not_withheld(client, db):
    """A non-medical violation (uncalibrated-zone language) is tolerated, mirroring
    the report path: the reply is logged and let through, not replaced. Withholding
    a whole helpful answer over a minor zone slip would be heavier-handed than the
    report path itself."""
    pack = _make_pack(metrics={"zones_calibrated": False}).model_dump(mode="json")
    reply = "Nice work holding Z2 for most of the run. Keep those easy days easy."
    resp = _post_chat_with_reply(client, _seed(db, context_pack=pack), reply)
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    # the zone language tripped rule 2 but is still delivered (soft = tolerate)
    assert _streamed_text(resp) == reply
    assert _assistant_text(db, activity) == reply


# --- unit: the shared rule bodies are applied to chat the same way ----------------


def test_medical_redirect_message_passes_the_validator():
    """The redirect that stands in for a gated turn must not itself trip the very
    rule it replaces — guards against a future reword shipping overreach."""
    assert check_medical_overreach(MEDICAL_REDIRECT_MESSAGE) == []


def test_validate_chat_text_degrades_safely_without_a_pack():
    """Rule 5 (medical scope) is pack-independent, so the floor holds even when the
    stored pack is empty/unparseable; the soft rules simply do not run."""
    medical = "You have a stress fracture; take 200mg ibuprofen."
    medical_rules = [v.rule for v in _validate_chat_text(medical, {})]
    assert "medical_overreach" in medical_rules

    clean = "Good steady run, your effort looked well controlled."
    assert _validate_chat_text(clean, {}) == []


# --- threat model: dangerous output must not reach the runner ---------------------


def test_overreach_split_across_chunks_is_still_gated(client, db):
    """The reason we buffer: a dangerous token can straddle two stream chunks, so
    neither chunk trips the rule alone. Validating the ASSEMBLED reply (not each
    chunk) is what catches it. Here the dose '200mg' is split into '...200' / 'mg...'
    — neither half matches, but the assembled text does, so the turn is gated."""
    chunks = ["For the soreness, take 200", "mg of ibuprofen after your run."]
    # sanity: neither chunk trips the rule on its own
    assert check_medical_overreach(chunks[0]) == []
    assert check_medical_overreach(chunks[1]) == []

    resp = _post_chat_with_chunks(client, _seed(db), chunks)
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    assert "200mg" not in resp.text and "ibuprofen" not in resp.text
    assert _streamed_text(resp) == MEDICAL_REDIRECT_MESSAGE
    assert _assistant_text(db, activity) == MEDICAL_REDIRECT_MESSAGE


def test_medical_overreach_wins_over_a_soft_violation(client, db):
    """When a reply trips BOTH a soft rule and the medical floor, the hard floor
    governs: the whole turn is withheld and replaced (the soft-tolerate path can
    never leak a medical overreach)."""
    pack = _make_pack(metrics={"zones_calibrated": False}).model_dump(mode="json")
    reply = "Hold Z2 on easy days. That ache is likely a stress fracture, get it checked."
    resp = _post_chat_with_reply(client, _seed(db, context_pack=pack), reply)
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    assert "stress fracture" not in resp.text
    assert _assistant_text(db, activity) == MEDICAL_REDIRECT_MESSAGE


def test_stream_error_serves_safe_message(client, db):
    """An LLM transport error mid-stream yields a safe canned message and persists
    it (behaviour preserved across the buffer-then-validate restructure); no partial
    pre-error tokens leak to the runner."""
    resp = _post_chat_raising(client, _seed(db))
    activity = db.query(Activity).first()

    assert resp.status_code == 200
    assert "Let me think" not in resp.text  # partial pre-error output is dropped
    streamed = _streamed_text(resp)
    assert "error" in streamed.lower()
    assert _assistant_text(db, activity) == streamed
