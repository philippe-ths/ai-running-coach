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
    _MAX_TOOL_ROUNDS,
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
    # Keep fetching for the whole budget, whatever the budget is: the guarantee
    # under test is that the LAST round carries no tools, not that there are N.
    rounds = [
        [{"name": "list_activities_in_range", "input": {"window": "last_7_days"}}]
        for _ in range(_MAX_TOOL_ROUNDS - 1)
    ] + ["Here's the summary."]
    stub = chat_tool_loop_stub(rounds, capture=capture)

    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "tell me everything")

    tools_seen = capture["tools_seen"]
    assert len(tools_seen) == _MAX_TOOL_ROUNDS
    assert all(t is CHAT_TOOLS for t in tools_seen[:-1])
    assert tools_seen[-1] is None  # final round: tools off
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
    """#648 f/u / #664: a turn that runs data tools banks a trace record per call on
    the assistant message — the resolved window and result count, not just the tool
    name — so the UI can render a persistent "looked up …" trace that survives a
    reload."""
    activity = _seed_activity_with_report(db)
    _past_run(db, activity.user_id, days_ago=5, distance_m=12000)

    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_30_days"}}],
        "Your longest recent run was 12.0 km.",
    ])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        events = await _drain(db, activity.id, "how far was my longest run?")

    # a tool_trace event carries WHAT was fetched: resolved window + count (#664),
    # all server-derived, so the runner can sanity-check the coach's data.
    traces = [ev.trace_entry for ev in events if ev.trace_entry is not None]
    assert traces == [{
        "tool": "list_activities_in_range",
        "label": "Looked up your training history",
        "detail": "last 30 days",
        "count": 1,
    }]

    # the assistant row banked that same record
    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id,
                CoachChatMessage.role == "assistant")
        .one()
    )
    assert saved.tools_used == [{
        "tool": "list_activities_in_range",
        "label": "Looked up your training history",
        "detail": "last 30 days",
        "count": 1,
    }]

    # and the history read surfaces it (from_attributes) for a reloaded UI
    history = get_chat_history(db, str(activity.id))
    assistant = [m for m in history if m.role == "assistant"][-1]
    assert assistant.tools_used is not None
    entry = assistant.tools_used[0]
    assert entry.tool == "list_activities_in_range"
    assert entry.detail == "last 30 days"
    assert entry.count == 1
    assert entry.label == "Looked up your training history"


@pytest.mark.asyncio
async def test_multi_window_fetch_shows_one_record_per_call(db):
    """#664: two list calls over different windows no longer collapse to one chip —
    the trace banks one record per CALL, so a genuine multi-window turn is honest."""
    activity = _seed_activity_with_report(db)
    _past_run(db, activity.user_id, days_ago=3, distance_m=9000)
    _past_run(db, activity.user_id, days_ago=40, distance_m=15000)

    stub = chat_tool_loop_stub([
        [{"name": "list_activities_in_range", "input": {"window": "last_7_days"}}],
        [{"name": "list_activities_in_range", "input": {"window": "last_90_days"}}],
        "You did one run this week and two in the last 90 days.",
    ])
    with patch.object(AnthropicClient, "stream_chat_turn", new=stub):
        await _drain(db, activity.id, "how much have I run lately?")

    saved = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity.id,
                CoachChatMessage.role == "assistant")
        .one()
    )
    # last_7_days sees only the 3-days-ago run; last_90_days also sweeps in the
    # 40-days-ago run and the seeded subject activity (~55 days ago). The point is
    # that the two windows are BOTH recorded, with their own counts, rather than
    # collapsing to one chip.
    assert saved.tools_used == [
        {"tool": "list_activities_in_range", "label": "Looked up your training history",
         "detail": "last 7 days", "count": 1},
        {"tool": "list_activities_in_range", "label": "Looked up your training history",
         "detail": "last 90 days", "count": 3},
    ]


@pytest.mark.asyncio
async def test_legacy_string_tools_used_coerced_on_read(db):
    """#664: a pre-#664 assistant row stored bare tool-name strings; the history read
    coerces them into records (filling the label) so a reloaded UI needs no legacy
    branch."""
    activity = _seed_activity_with_report(db)
    db.add(CoachChatMessage(
        activity_id=activity.id, role="assistant", content="old turn",
        tools_used=["list_activities_in_range", "get_training_summary"],
    ))
    db.commit()

    history = get_chat_history(db, str(activity.id))
    assistant = [m for m in history if m.role == "assistant"][-1]
    assert [e.tool for e in assistant.tools_used] == [
        "list_activities_in_range", "get_training_summary"]
    assert assistant.tools_used[0].label == "Looked up your training history"
    # legacy rows have no stored window/count
    assert assistant.tools_used[0].detail is None
    assert assistant.tools_used[0].count is None


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
