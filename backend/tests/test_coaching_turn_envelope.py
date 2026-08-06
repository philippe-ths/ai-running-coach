"""The coaching-turn envelope (#801) and the spend it must never lose (#786).

The gap this pins: `thread_turn` checked `over_budget` before a turn and never
recorded against it, so the cap only ever tripped on spend OTHER paths had
recorded — on the most token-hungry surface the product has. It was inherited
from the retired activity chat box, so it was never introduced by a bug; it is
what happens when "record your spend" is a convention each call site must
remember rather than a property of the client a turn is handed.

These tests assert the property, not the patch: metering is on the client, so
every path that takes a turn client is metered, including one built later.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services.coach import budget, turn
from app.services.coach.llm import ChatTurnDelta, MessageResult, Usage
from app.services.coach.turn import MeteredClient, TurnKind, build_client, resolve_model


# --- model resolution: one place -------------------------------------------


def test_report_kinds_run_on_the_coach_model(monkeypatch):
    monkeypatch.setattr(settings, "COACH_MODEL_ID", "claude-sonnet-4-6")
    monkeypatch.setattr(settings, "COACH_CHAT_MODEL_ID", "claude-haiku-4-5")
    for kind in (TurnKind.REPORT, TurnKind.OPENER, TurnKind.FULLER):
        assert resolve_model(kind) == "claude-sonnet-4-6"


def test_thread_turn_runs_on_the_chat_model_when_one_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "COACH_MODEL_ID", "claude-sonnet-4-6")
    monkeypatch.setattr(settings, "COACH_CHAT_MODEL_ID", "claude-haiku-4-5")
    assert resolve_model(TurnKind.THREAD) == "claude-haiku-4-5"


def test_unset_chat_model_falls_back_to_the_coach_model(monkeypatch):
    """Day-one behaviour: with COACH_CHAT_MODEL_ID unset every turn runs on the
    same model, so introducing the chat lane changed nothing by itself."""
    monkeypatch.setattr(settings, "COACH_MODEL_ID", "claude-sonnet-4-6")
    monkeypatch.setattr(settings, "COACH_CHAT_MODEL_ID", "")
    assert resolve_model(TurnKind.THREAD) == "claude-sonnet-4-6"


def test_build_client_meters_and_carries_the_resolved_model(monkeypatch):
    monkeypatch.setattr(settings, "COACH_MODEL_ID", "claude-sonnet-4-6")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    client = build_client(TurnKind.FULLER, "user-1")
    assert isinstance(client, MeteredClient)
    assert client.model == "claude-sonnet-4-6"


# --- metering: every call, every method ------------------------------------


@pytest.fixture
def gate():
    g = budget.new_in_memory_gate()
    budget.set_gate(g)
    yield g
    budget.set_gate(None)


def _inner(model="claude-opus-4-8"):
    inner = MagicMock()
    inner.model = model
    return inner


def test_message_call_records_its_own_spend(gate, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    inner = _inner()
    inner.generate_coach_message = AsyncMock(
        return_value=MessageResult(
            content_blocks=[], stop_reason="end_turn",
            input_tokens=1_000_000, output_tokens=0,
        )
    )
    client = MeteredClient(inner, "user-A")
    asyncio.run(client.generate_coach_message(system="s", user="u", tools=[]))
    assert gate.over_budget("user-A") is True
    assert gate.over_budget("user-B") is False  # never another runner's counter


def test_every_sub_call_is_counted_not_just_the_first(gate, monkeypatch):
    """The retry / escalation / policy-fix fan-out is the real cost lever, so
    each sub-call accrues rather than the turn accruing once."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 6.0)
    inner = _inner()
    inner.generate_coach_message = AsyncMock(
        return_value=MessageResult(
            content_blocks=[], stop_reason="end_turn",
            input_tokens=500_000, output_tokens=0,  # $2.50 each on Opus input
        )
    )
    client = MeteredClient(inner, "user-A")
    for _ in range(2):
        asyncio.run(client.generate_coach_message(system="s", user="u", tools=[]))
    assert gate.over_budget("user-A") is False  # $5.00 of a $6 ceiling
    asyncio.run(client.generate_coach_message(system="s", user="u", tools=[]))
    assert gate.over_budget("user-A") is True   # $7.50


def test_structured_json_call_records_its_spend(gate, monkeypatch):
    """The legacy structured report family gated on the cap and never recorded
    either — the same defect as #786, one prompt family over."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    inner = _inner()
    inner.generate_json_with_usage = AsyncMock(
        return_value=("{}", Usage(input_tokens=1_000_000, output_tokens=0))
    )
    client = MeteredClient(inner, "user-A")
    asyncio.run(client.generate_json("s", "u"))
    assert gate.over_budget("user-A") is True


def test_forced_tool_call_records_its_spend(gate, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    inner = _inner()
    inner.generate_structured_with_usage = AsyncMock(
        return_value=({"a": 1}, Usage(input_tokens=1_000_000, output_tokens=0))
    )
    client = MeteredClient(inner, "user-A")
    asyncio.run(client.generate_structured(system="s", user="u", tool={"name": "t"}))
    assert gate.over_budget("user-A") is True


def test_streaming_turn_records_each_round(gate, monkeypatch):
    """#786 proper: the conversational turn. Each ROUND of the bounded tool loop
    records, so a multi-round turn costs what it costs — and a turn that fails
    mid-loop still accrues the rounds it really spent."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 4.0)
    inner = _inner()

    async def _stream(*, system, messages, tools=None, max_tokens=1024):
        yield ChatTurnDelta(text="hi")
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[], stop_reason="end_turn",
                input_tokens=500_000, output_tokens=0,  # $2.50 per round
            )
        )

    inner.stream_chat_turn = _stream
    client = MeteredClient(inner, "user-A")

    async def drive():
        out = []
        async for delta in client.stream_chat_turn(system="s", messages=[]):
            out.append(delta)
        return out

    deltas = asyncio.run(drive())
    assert [d.text for d in deltas if d.text] == ["hi"]  # deltas pass through
    assert gate.over_budget("user-A") is False  # $2.50 of $4
    asyncio.run(drive())
    assert gate.over_budget("user-A") is True   # $5.00


def test_cache_buckets_reach_the_counter(gate, monkeypatch):
    """A cached prefix is most of a conversational turn's input. If the cache
    buckets do not ride the result the counter silently under-reports it."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    inner = _inner()

    async def _stream(*, system, messages, tools=None, max_tokens=1024):
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[], stop_reason="end_turn",
                input_tokens=0, output_tokens=0,
                cache_creation_input_tokens=1_000_000,  # $6.25 on Opus
            )
        )

    inner.stream_chat_turn = _stream
    client = MeteredClient(inner, "user-A")

    async def drive():
        async for _ in client.stream_chat_turn(system="s", messages=[]):
            pass

    asyncio.run(drive())
    assert gate.over_budget("user-A") is True


def test_a_backend_failure_inside_the_gate_is_swallowed(gate, monkeypatch):
    """The real protection: BudgetGate.record swallows its backend's errors, so
    a Redis blip never fails a coaching turn."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    broken = SimpleNamespace(
        incr=MagicMock(side_effect=RuntimeError("redis down")),
        get=MagicMock(return_value=0.0),
    )
    budget.set_gate(budget.BudgetGate(broken))
    inner = _inner()
    inner.generate_coach_message = AsyncMock(
        return_value=MessageResult(content_blocks=[], stop_reason="end_turn",
                                   input_tokens=10, output_tokens=10)
    )
    client = MeteredClient(inner, "user-A")
    result = asyncio.run(client.generate_coach_message(system="s", user="u", tools=[]))
    assert result.stop_reason == "end_turn"


def test_no_user_means_no_metering(gate, monkeypatch):
    """A turn with nobody to bill records nothing rather than billing a
    placeholder key."""
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 0.000001)
    inner = _inner()
    inner.generate_coach_message = AsyncMock(
        return_value=MessageResult(content_blocks=[], stop_reason="end_turn",
                                   input_tokens=1_000_000, output_tokens=0)
    )
    asyncio.run(
        MeteredClient(inner, None).generate_coach_message(system="s", user="u", tools=[])
    )
    assert gate.over_budget("user-A") is False


# --- #786 end to end: a real thread turn records what it spent --------------


@pytest.mark.asyncio
async def test_thread_turn_records_its_spend_end_to_end(db, gate, monkeypatch):
    """The defect as filed: `over_budget` was called before a turn and nothing
    was ever recorded after it, so a runner could converse without limit while
    the gate waited on spend that only other paths reported.

    Driven through the real `stream_thread_turn`, not the client in isolation,
    because the thing that was broken was the WIRING, not the counter.
    """
    from app.models import User
    from app.services.coach.llm import AnthropicClient
    from app.services.coach.thread_turn import stream_thread_turn
    from tests.test_coach_chat_stream import _seed_activity_with_report

    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 100.0)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    activity = _seed_activity_with_report(db)
    user = db.query(User).filter(User.id == activity.user_id).one()

    async def _stream(self, *, system, messages, tools=None, max_tokens=1024):
        yield ChatTurnDelta(text="Solid run.")
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[{"type": "text", "text": "Solid run."}],
                stop_reason="end_turn",
                input_tokens=400_000, output_tokens=10_000,
            )
        )

    with patch.object(AnthropicClient, "stream_chat_turn", _stream):
        events = [
            ev
            async for ev in stream_thread_turn(
                db, user, message="How did that go?", anchor_activity=activity
            )
        ]

    assert "Solid run." in "".join(ev.text for ev in events if ev.text)
    # The turn's spend is now visible to the very gate it consulted.
    today = datetime.now(timezone.utc).date().isoformat()
    assert gate._backend.get(gate._key(f"user:{user.id}", today)) > 0


@pytest.mark.asyncio
async def test_thread_turn_spend_can_trip_the_cap_on_a_later_turn(db, gate, monkeypatch):
    """The point of recording: conversation is now self-limiting. Before this,
    no number of turns could ever move the runner towards their own ceiling."""
    from app.models import User
    from app.services.coach.llm import AnthropicClient
    from app.services.coach.thread_turn import (
        THREAD_BUDGET_PAUSED_MESSAGE,
        stream_thread_turn,
    )
    from tests.test_coach_chat_stream import _seed_activity_with_report

    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 1.0)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    activity = _seed_activity_with_report(db)
    user = db.query(User).filter(User.id == activity.user_id).one()

    async def _stream(self, *, system, messages, tools=None, max_tokens=1024):
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[{"type": "text", "text": "Solid run."}],
                stop_reason="end_turn",
                input_tokens=1_000_000, output_tokens=0,
            )
        )

    async def one_turn():
        return [
            ev
            async for ev in stream_thread_turn(
                db, user, message="How did that go?", anchor_activity=activity
            )
        ]

    with patch.object(AnthropicClient, "stream_chat_turn", _stream):
        first = await one_turn()
        second = await one_turn()

    assert "Solid run." in "".join(ev.text for ev in first if ev.text)
    assert "".join(ev.text for ev in second if ev.text) == THREAD_BUDGET_PAUSED_MESSAGE


# --- the relationship read has ONE gate (#791) ------------------------------


def test_relationship_read_is_gated_once(db, monkeypatch):
    from app.models import User
    from app.models.coaching_relationship import CoachingRelationship

    user = User(email="r@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(CoachingRelationship(user_id=user.id))
    db.commit()

    monkeypatch.setattr(settings, "COACH_RELATIONSHIP_ENABLED", True)
    assert turn.relationship_for_user(db, user.id) is not None

    monkeypatch.setattr(settings, "COACH_RELATIONSHIP_ENABLED", False)
    assert turn.relationship_for_user(db, user.id) is None
