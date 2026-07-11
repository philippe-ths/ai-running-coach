"""I2: chat is a continuation of the coaching relationship, not a generic chatbot.

The chat coach must speak in the runner's declared Voice and carry the same
relationship-memory authority disciplines (narrative / corpus / user-materials /
believed-facts / training-load) that the report coach carries, so it is the SAME
coach the runner already heard from. Storage stays per-activity; this is the
read-side shared-memory unification (epic #177, I2).
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.chat import _build_chat_system_prompt

VOICE_AWARE_PROMPT = "coach_message_v7"
NON_VOICE_PROMPT = "coach_report_v10"


def _seed(db, *, voice_preset=None) -> Activity:
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
    if voice_preset is not None:
        db.add(CoachingRelationship(user_id=user.id, voice_preset=voice_preset))
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


def _capture_system(client, db, activity):
    """POST a chat turn with a mocked LLM and return the system prompt it received."""
    from tests._chat_stubs import chat_turn_stub

    captured = {}

    with patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=chat_turn_stub(["ok"], capture=captured),
    ):
        resp = client.post(
            f"/api/activities/{activity.id}/coach-chat",
            json={"message": "How did I do?"},
        )
    assert resp.status_code == 200
    return captured["system"]


# --- the static disciplines (pure prompt-assembly) -------------------------------


def test_chat_prompt_carries_relationship_memory_disciplines():
    """The chat prompt always carries the authority-tiering disciplines, so the chat
    coach treats every relationship-memory section exactly as the report coach does."""
    prompt = _build_chat_system_prompt(
        context_pack={}, report={}, profile={}, trends={}, splits=[], voice_block="",
    )
    assert "AUTHORITY TIERING" in prompt
    # each relationship-memory section the stored pack can carry is disciplined
    for section in ("narrative", "corpus", "believed_facts", "training_load"):
        assert section in prompt, f"missing discipline for {section}"
    # user materials are reference the coach reasons over, never instructions
    assert "never instructions" in prompt.lower()
    # measured data and the safety floor are the top tier
    assert "safety floor" in prompt.lower()


def test_chat_prompt_embeds_voice_block_when_present():
    block = "\n\n## YOUR VOICE FOR THIS RUNNER\nDIALS (1 = low pole, 5 = high pole):"
    prompt = _build_chat_system_prompt(
        context_pack={}, report={}, profile={}, trends={}, splits=[], voice_block=block,
    )
    assert "## YOUR VOICE FOR THIS RUNNER" in prompt


# --- end-to-end wiring through the streaming endpoint ----------------------------


def test_chat_speaks_in_declared_voice_under_voice_aware_prompt(client, db, monkeypatch):
    """A runner with a declared Voice preset gets that voice injected into chat."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", VOICE_AWARE_PROMPT)
    activity = _seed(db, voice_preset="cornerman")
    system = _capture_system(client, db, activity)
    # the rendered block (## heading) is injected, not merely mentioned by the rules
    assert "## YOUR VOICE FOR THIS RUNNER" in system
    assert "PRESET:" in system  # the declared preset flowed through


def test_chat_uses_default_voice_when_undeclared_under_voice_aware_prompt(client, db, monkeypatch):
    """An undeclared runner still gets the explicit moderate-default voice block."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", VOICE_AWARE_PROMPT)
    activity = _seed(db, voice_preset=None)
    system = _capture_system(client, db, activity)
    assert "## YOUR VOICE FOR THIS RUNNER" in system
    assert "default moderate coaching voice" in system


def test_chat_omits_voice_block_under_non_voice_prompt(client, db, monkeypatch):
    """Voice gating mirrors the report: no voice-aware active prompt, no voice block.
    The relationship-memory disciplines stay (they are always-on, harmless when a
    section is absent)."""
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", NON_VOICE_PROMPT)
    activity = _seed(db, voice_preset="cornerman")
    system = _capture_system(client, db, activity)
    assert "## YOUR VOICE FOR THIS RUNNER" not in system  # rendered block absent
    assert "AUTHORITY TIERING" in system
