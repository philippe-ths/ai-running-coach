"""Service-layer tests for the A3 prose-message generation path (deliverable 5).

Exercises the message family end-to-end against a stubbed generate_coach_message:
schema-family routing, storage shape, the digest branch, degrade-not-withhold,
fallback paths, and the medical-overreach-forces-fallback strengthening (ADR 0009).
No live API call.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import anthropic
import httpx
import pytest

from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.models.coach_report import CoachReport
from app.services.coach.digest import _first_sentence, build_report_digest
from app.services.coach.llm import MessageResult
from app.services.coach.service import (
    active_schema_version,
    get_or_generate_coach_report,
)


# --- active_schema_version (unit) --------------------------------------------

class TestActiveSchemaVersion:
    def test_legacy_family_is_1_2(self):
        assert active_schema_version("coach_report_v10") == "1.2"

    def test_message_family_is_2_0(self):
        assert active_schema_version("coach_message_v1") == "2.0"

    def test_unknown_prefix_defaults_to_legacy(self):
        assert active_schema_version("something_else_v1") == "1.2"


# --- digest branch (unit) ----------------------------------------------------

class TestDigestMessageBranch:
    def test_first_sentence_lifts_opening(self):
        assert _first_sentence("Strong steady run. The rest is detail.") == "Strong steady run."

    def test_first_sentence_empty(self):
        assert _first_sentence("   ") is None

    def test_message_digest_lead_is_first_sentence(self):
        report = {
            "message": "Solid aerobic long run. HR drift stayed low.",
            "headline": "Solid long run",
            "next_steps": [{"action": "Easy day", "details": "30 min", "why": "Recover"}],
        }
        digest = build_report_digest(report, datetime(2026, 5, 27, 10, 0, 0))
        assert digest.lead_argument == "Solid aerobic long run."
        assert digest.headline == "Solid long run"
        assert digest.next_steps == ["Easy day (30 min)"]

    def test_structured_digest_still_uses_lead_argument(self):
        report = {
            "headline": "Tempo",
            "lead_argument": {"text": "You held threshold well."},
            "next_steps": [],
        }
        digest = build_report_digest(report, datetime(2026, 5, 27, 10, 0, 0))
        assert digest.lead_argument == "You held threshold well."


# --- service path fixtures ---------------------------------------------------

def _seed_activity_with_metrics(db) -> Activity:
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
    db.commit()
    db.refresh(activity)
    return activity


def _text(t):
    return {"type": "text", "text": t}


def _tail(**tail):
    return {"type": "tool_use", "name": "record_coach_tail", "input": tail}


def _result(blocks, stop_reason="end_turn"):
    return MessageResult(content_blocks=blocks, stop_reason=stop_reason)


def _message_client(blocks, stop_reason="end_turn"):
    fake = AsyncMock()
    fake.generate_coach_message = AsyncMock(return_value=_result(blocks, stop_reason))
    return fake


@pytest.fixture
def _message_prompt(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v1")
    return settings


def _stored(db, activity):
    return db.query(CoachReport).filter(CoachReport.activity_id == activity.id).first()


# --- service path tests ------------------------------------------------------

class TestMessageServicePath:
    @pytest.mark.asyncio
    async def test_happy_path_stores_message_under_schema_2_0(self, db, _message_prompt):
        activity = _seed_activity_with_metrics(db)
        blocks = [
            _text("Nice steady aerobic run. You held your effort well throughout."),
            _tail(
                headline="Solid easy run",
                next_steps=[{"action": "Easy run", "details": "30 min", "why": "Recovery"}],
                risks=[],
                questions=[],
            ),
        ]
        with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
            read = await get_or_generate_coach_report(db, str(activity.id))

        assert read is not None
        assert read.report.message.startswith("Nice steady aerobic run")
        stored = _stored(db, activity)
        assert stored.schema_version == "2.0"
        assert stored.prompt_id == "coach_message_v1"
        assert stored.is_fallback is False
        assert stored.report["message"].startswith("Nice steady aerobic run")
        assert stored.report["tail_degraded"] is False
        assert stored.meta["tail_degraded"] is False
        # Digest persisted with the message's first sentence as lead.
        assert stored.digest["lead_argument"] == "Nice steady aerobic run."
        assert stored.digest["headline"] == "Solid easy run"

    @pytest.mark.asyncio
    async def test_degraded_tail_keeps_message(self, db, _message_prompt):
        """No tool block -> the message survives with tail_degraded=True and empty
        next_steps (the loop then abstains). A corrective retry fires first."""
        activity = _seed_activity_with_metrics(db)
        blocks = [_text("Good easy run, nothing to flag today.")]
        client = _message_client(blocks, stop_reason="end_turn")
        with patch("app.services.coach.service.AnthropicClient", return_value=client):
            read = await get_or_generate_coach_report(db, str(activity.id))

        assert read.report.message.startswith("Good easy run")
        stored = _stored(db, activity)
        assert stored.is_fallback is False
        assert stored.report["tail_degraded"] is True
        assert stored.report["next_steps"] == []
        assert stored.meta["tail_degraded"] is True
        # The tail-skip corrective retry fired (>= 2 calls): initial + the nudge,
        # plus possibly a policy retry (this pack's null check-in trips rule 1).
        assert client.generate_coach_message.await_count >= 2

    @pytest.mark.asyncio
    async def test_empty_message_yields_fallback(self, db, _message_prompt):
        activity = _seed_activity_with_metrics(db)
        blocks = [_text("   ")]  # whitespace only -> EmptyMessageError
        with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
            read = await get_or_generate_coach_report(db, str(activity.id))

        stored = _stored(db, activity)
        assert stored.is_fallback is True
        assert stored.schema_version == "2.0"
        assert "couldn't put together" in stored.report["message"]
        assert stored.report["tail_degraded"] is True
        # A tail is never stored without a message.
        assert stored.report["message"].strip() != ""

    @pytest.mark.asyncio
    async def test_refusal_yields_fallback(self, db, _message_prompt):
        activity = _seed_activity_with_metrics(db)
        blocks = [_text("I can't help with that.")]
        with patch("app.services.coach.service.AnthropicClient",
                   return_value=_message_client(blocks, stop_reason="refusal")):
            read = await get_or_generate_coach_report(db, str(activity.id))

        stored = _stored(db, activity)
        assert stored.is_fallback is True
        assert stored.schema_version == "2.0"

    @pytest.mark.asyncio
    async def test_transport_error_yields_fallback(self, db, _message_prompt):
        activity = _seed_activity_with_metrics(db)
        err = anthropic.APITimeoutError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        fake = AsyncMock()
        fake.generate_coach_message = AsyncMock(side_effect=err)
        with patch("app.services.coach.service.AnthropicClient", return_value=fake):
            read = await get_or_generate_coach_report(db, str(activity.id))

        stored = _stored(db, activity)
        assert stored.is_fallback is True
        assert stored.schema_version == "2.0"

    @pytest.mark.asyncio
    async def test_persistent_medical_overreach_forces_fallback(self, db, _message_prompt):
        """ADR 0009: medical overreach surviving the retry forces is_fallback=True
        (prose renders verbatim), unlike the structured path's store-with-a-flag."""
        activity = _seed_activity_with_metrics(db)
        blocks = [
            _text("Your shin pain is consistent with a stress fracture; you should rest it."),
            _tail(headline="Concern", next_steps=[], risks=[], questions=[]),
        ]
        # Same overreaching output on every call (initial + retry), so it persists.
        with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
            read = await get_or_generate_coach_report(db, str(activity.id))

        stored = _stored(db, activity)
        assert stored.is_fallback is True
        assert "couldn't put together" in stored.report["message"]

    @pytest.mark.asyncio
    async def test_degraded_tail_emits_monitor_warning(self, db, _message_prompt, caplog):
        """The single greppable monitor signal: a stored degraded tail logs a
        `coach_tail_degraded` WARNING (visible in prod logs / Sentry)."""
        import logging
        activity = _seed_activity_with_metrics(db)
        blocks = [_text("Good easy run, nothing to flag.")]  # no tail block -> degrades
        with caplog.at_level(logging.WARNING, logger="app.services.coach.service"):
            with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
                await get_or_generate_coach_report(db, str(activity.id))
        assert any("coach_tail_degraded" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_clean_tail_does_not_emit_monitor_warning(self, db, _message_prompt, caplog):
        import logging
        activity = _seed_activity_with_metrics(db)
        blocks = [
            _text("Solid steady run. You held the effort well."),
            _tail(headline="Solid run", next_steps=[], risks=[], questions=[]),
        ]
        with caplog.at_level(logging.WARNING, logger="app.services.coach.service"):
            with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
                await get_or_generate_coach_report(db, str(activity.id))
        assert not any("coach_tail_degraded" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_message_family_does_not_writeback_on_fallback(self, db, _message_prompt):
        """Fallbacks must not feed the learning loop (no digest stored)."""
        activity = _seed_activity_with_metrics(db)
        blocks = [_text("   ")]
        with patch("app.services.coach.service.AnthropicClient", return_value=_message_client(blocks)):
            await get_or_generate_coach_report(db, str(activity.id))
        stored = _stored(db, activity)
        assert stored.is_fallback is True
        assert stored.digest is None
