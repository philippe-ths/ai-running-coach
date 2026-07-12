"""#648: the coach-chat agentic tool loop inside `stream_chat_response`.

Drives the streaming coroutine directly (TestClient buffers the SSE body, so it is
blind to the intermediate status frames these tests inspect). Uses a scripted
`stream_chat_turn` stub to pin the loop mechanics WITHOUT a real LLM: a tool round
executes owner-scoped and feeds its result back, the final round runs tools-off so
the loop terminates, and the medical-scope floor still gates the final reply.
"""

from datetime import datetime, timedelta, date
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models import Activity, CoachChatMessage, DerivedMetric
from app.services.coach.chat import (
    MEDICAL_REDIRECT_MESSAGE,
    get_chat_history,
    stream_chat_response,
)
from app.services.coach.llm import AnthropicClient
from app.services.coach.query_tools import CHAT_TOOLS
from tests._chat_stubs import chat_tool_loop_stub
from tests.test_coach_chat_stream import _seed_activity_with_report


def _past_run(db, user_id, *, days_ago, distance_m=8000, moving_time_s=2400):
    """A prior activity for a tool to find, dated relative to today so it lands in
    the queried window regardless of the calendar day the test runs."""
    start = datetime.now() - timedelta(days=days_ago)
    a = Activity(
        user_id=user_id, strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=start, type="Run", name="r", distance_m=distance_m,
        moving_time_s=moving_time_s, elapsed_time_s=moving_time_s, elev_gain_m=5.0,
        avg_hr=140, avg_cadence=168, raw_summary={},
    )
    db.add(a)
    db.commit()
    db.add(DerivedMetric(
        activity_id=a.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=55.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.commit()
    return a


async def _drain(db, activity_id, message):
    return [ev async for ev in stream_chat_response(db, str(activity_id), message)]


@pytest.mark.asyncio
async def test_tool_loop_fetches_then_answers(db):
    activity = _seed_activity_with_report(db)
    _past_run(db, activity.user_id, days_ago=5, distance_m=12000)

    capture = {}
    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_30_days"}}],
        "Your longest recent run was 12.0 km.",
    ], capture=capture)

    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "How far was my longest run?")

    # the ephemeral affordance for the list tool surfaced during the fetch
    assert any(ev.status_label == "Checking your training history…" for ev in events)
    # the final reply streamed to the runner
    text = "".join(ev.text for ev in events if ev.text)
    assert "12.0 km" in text
    # the tool ACTUALLY executed against the DB and its result fed back into round 2
    round2_msgs = capture["messages_seen"][1]
    tool_results = [
        blk for m in round2_msgs if isinstance(m.get("content"), list)
        for blk in m["content"] if isinstance(blk, dict) and blk.get("type") == "tool_result"
    ]
    assert tool_results, "the tool_result was fed back into the next round"
    assert "12.0" in tool_results[0]["content"], "the fetched data reached the model"


@pytest.mark.asyncio
async def test_final_round_runs_tools_off(db):
    """The loop always terminates: after the round budget, the final call carries no
    tools so the model must answer in text rather than fetch again."""
    activity = _seed_activity_with_report(db)
    capture = {}
    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_7_days"}}],
        [{"name": "list_activities_in_range", "input": {"window": "last_30_days"}}],
        "Here's the summary.",
    ], capture=capture)

    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "tell me everything")

    tools_seen = capture["tools_seen"]
    assert tools_seen[0] is CHAT_TOOLS
    assert tools_seen[1] is CHAT_TOOLS
    assert tools_seen[2] is None  # final round: tools off
    assert "Here's the summary." in "".join(ev.text for ev in events if ev.text)


@pytest.mark.asyncio
async def test_medical_floor_holds_after_a_tool_round(db):
    """The #340 safety floor is unchanged by the loop: a medical-overreach FINAL
    reply is withheld and replaced by the safe redirect, even after a tool round."""
    activity = _seed_activity_with_report(db)
    _past_run(db, activity.user_id, days_ago=3)
    bad = "Honestly this looks like a stress fracture. Take 200mg of ibuprofen before your next run."

    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_7_days"}}],
        bad,
    ])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "my shin hurts")

    text = "".join(ev.text for ev in events if ev.text)
    assert text == MEDICAL_REDIRECT_MESSAGE
    assert "200mg" not in text


@pytest.mark.asyncio
async def test_direct_answer_without_a_tool_call(db):
    """A question needing no lookup answers in one round — no tool call, no status
    affordance — so the loop never regresses the plain chat path."""
    activity = _seed_activity_with_report(db)
    stub = chat_tool_loop_stub(["Nice easy run today, well controlled."])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "how did I do?")

    assert not any(ev.status_label for ev in events)
    assert "Nice easy run today, well controlled." in "".join(ev.text for ev in events if ev.text)


@pytest.mark.asyncio
async def test_tools_used_persisted_and_returned_in_history(db):
    """#648 f/u: a turn that runs data tools banks their names on the assistant
    message and every status frame carries the tool name, so the UI can render a
    persistent trace that survives a reload."""
    activity = _seed_activity_with_report(db)
    _past_run(db, activity.user_id, days_ago=5, distance_m=12000)

    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_30_days"}}],
        "Your longest recent run was 12.0 km.",
    ])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "how far was my longest run?")

    # the status frame carries the tool name for the live/persistent trace
    assert any(ev.status_tool == "list_activities_in_range" for ev in events)

    # the assistant row banked the tool it ran
    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id,
                CoachChatMessage.role == "assistant")
        .one()
    )
    assert saved.tools_used == ["list_activities_in_range"]

    # and the history read surfaces it (from_attributes) for a reloaded UI
    history = get_chat_history(db, str(activity.id))
    assistant = [m for m in history if m.role == "assistant"][-1]
    assert assistant.tools_used == ["list_activities_in_range"]


@pytest.mark.asyncio
async def test_tools_used_null_when_no_fetch(db):
    """A no-tool turn stores tools_used as null, so the trace renders nothing rather
    than an empty chip row."""
    activity = _seed_activity_with_report(db)
    stub = chat_tool_loop_stub(["Nice easy run, nothing to fetch."])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        await _drain(db, activity.id, "how did I do?")

    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id,
                CoachChatMessage.role == "assistant")
        .one()
    )
    assert saved.tools_used is None
