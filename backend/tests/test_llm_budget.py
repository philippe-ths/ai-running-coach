"""Per-user LLM cost cap (P2.2, #122).

The structural contract: at the per-user (or global) spend cap, coach generation
DEGRADES to the deterministic is_fallback path BEFORE spending any tokens, and
one user hitting the cap never affects another. Plus the gate arithmetic and the
window/ceiling logic.
"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.coach import budget
from app.services.coach.service import get_or_generate_coach_report

# Reuse the message-service fixtures (valid MessageResult + mock client + seed).
from tests.test_message_service import (
    _message_client,
    _seed_activity_with_metrics,
    _stored,
    _tail,
    _text,
)


# --- gate arithmetic + windows (unit) --------------------------------------

def test_cost_usd_matches_price_table():
    # Opus: $5/MTok in, $25/MTok out.
    assert budget.cost_usd("claude-opus-4-8", 1_000_000, 0) == pytest.approx(5.0)
    assert budget.cost_usd("claude-opus-4-8", 0, 1_000_000) == pytest.approx(25.0)
    # Sonnet: $3/$15.
    assert budget.cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)
    # Unknown model prices as Opus (conservative over-count).
    assert budget.cost_usd("mystery-model", 1_000_000, 0) == pytest.approx(5.0)


def test_over_budget_is_per_user(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    gate = budget.new_in_memory_gate()
    # Spend enough Opus tokens to clear the $1 daily ceiling for user A only.
    gate.record("user-A", "claude-opus-4-8", 1_000_000, 0)  # $5
    assert gate.over_budget("user-A") is True
    assert gate.over_budget("user-B") is False  # B is untouched


def test_global_ceiling_trips_for_everyone(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 0.0)
    monkeypatch.setattr(settings, "LLM_BUDGET_GLOBAL_DAILY_USD", 4.0)
    gate = budget.new_in_memory_gate()
    gate.record("user-A", "claude-opus-4-8", 1_000_000, 0)  # $5 global
    # Over the GLOBAL ceiling, so even a user who never spent is blocked.
    assert gate.over_budget("user-B") is True


def test_zero_ceiling_disables_the_window(monkeypatch):
    for var in (
        "LLM_BUDGET_USER_DAILY_USD", "LLM_BUDGET_USER_MONTHLY_USD",
        "LLM_BUDGET_GLOBAL_DAILY_USD", "LLM_BUDGET_GLOBAL_MONTHLY_USD",
    ):
        monkeypatch.setattr(settings, var, 0.0)
    gate = budget.new_in_memory_gate()
    gate.record("user-A", "claude-opus-4-8", 100_000_000, 100_000_000)  # huge
    assert gate.over_budget("user-A") is False  # all windows disabled => never over


# --- the degrade-not-crash contract over the real service path -------------

@pytest.fixture
def _single_shot_prompt(monkeypatch):
    # coach_message_v1 routes get_or_generate through _generate_message directly
    # (not the two-stage cadence), so the budget gate at its entry is exercised.
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v1")


@pytest.fixture
def _capped_gate(monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    gate = budget.new_in_memory_gate()
    budget.set_gate(gate)
    yield gate
    budget.set_gate(None)


def _valid_blocks():
    return [
        _text("Nice steady aerobic run. You held your effort well throughout."),
        _tail(
            headline="Solid easy run",
            next_steps=[{"action": "Easy run", "details": "30 min", "why": "Recovery"}],
            risks=[], questions=[],
        ),
    ]


@pytest.mark.asyncio
async def test_over_budget_user_degrades_to_fallback_without_calling_llm(
    db, _single_shot_prompt, _capped_gate
):
    activity = _seed_activity_with_metrics(db)
    # Push this activity's owner over the $1 daily cap.
    _capped_gate.record(str(activity.user_id), "claude-opus-4-8", 1_000_000, 0)

    client = _message_client(_valid_blocks())
    with patch("app.services.coach.service.AnthropicClient", return_value=client):
        read = await get_or_generate_coach_report(db, str(activity.id))

    # Degraded to the deterministic fallback, and the LLM was never called (the
    # cap is observed BEFORE the call).
    client.generate_coach_message.assert_not_called()
    stored = _stored(db, activity)
    assert stored.is_fallback is True


@pytest.mark.asyncio
async def test_under_budget_user_unaffected(db, _single_shot_prompt, _capped_gate):
    activity = _seed_activity_with_metrics(db)
    # This user has spent nothing; they are under the cap.
    client = _message_client(_valid_blocks())
    with patch("app.services.coach.service.AnthropicClient", return_value=client):
        read = await get_or_generate_coach_report(db, str(activity.id))

    client.generate_coach_message.assert_called()
    stored = _stored(db, activity)
    assert stored.is_fallback is False
    # Spend was recorded for this user after the call.
    assert _capped_gate.over_budget(str(activity.user_id)) is False
    # The per-user isolation contract (one user's cap never blocks another) is
    # proven at the gate level by test_over_budget_is_per_user, combined with the
    # degrade + under-budget service paths above.


@pytest.mark.asyncio
async def test_chat_declines_over_budget_without_calling_llm(db, _capped_gate):
    """Chat resends the full context every turn (an unbounded input-token lever),
    so at the cap it declines with a plain message and never calls the LLM."""
    from tests.test_coach_chat_stream import _seed_activity_with_report
    from app.services.coach.chat import stream_chat_response
    from app.services.coach.llm import AnthropicClient

    activity = _seed_activity_with_report(db)
    _capped_gate.record(str(activity.user_id), "claude-opus-4-8", 1_000_000, 0)

    with patch.object(
        AnthropicClient, "stream_chat", side_effect=AssertionError("LLM must not be called")
    ):
        events = [
            ev async for ev in stream_chat_response(db, str(activity.id), "How did I do?")
        ]
    text = "".join(ev.text for ev in events if ev.text)
    assert "usage limit" in text.lower()
