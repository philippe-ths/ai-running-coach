"""The thread turn (#766, ADR 0027): one coach reply in a runner-initiated Thread.

A thread turn is anchored to the RUNNER and to NOW, not to an activity, so it
assembles a relationship baseline — the runner-and-now sibling of
`build_b_baseline` — instead of borrowing a stored report's context pack:
profile, the runner memory profile (the citable Stated-memory tier), a current
readiness read, the runner's declared Voice, and a bounded digest of their other
recent threads. Everything deeper is fetched on demand through owner-scoped
query tools.

The generation core and the safety floor live in `chat.py`
(`_buffered_tool_loop`, `_validate_conversational_text`): one buffer-then-
validate loop, one medical-scope floor, re-sourced from the turn's own facts.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.activity import Activity
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.coach import threads as thread_service
from app.schemas.thread import ScreenPointer
from app.services.coach.chat import (
    ChatStreamEvent,
    MEDICAL_REDIRECT_MESSAGE,
    _buffered_tool_loop,
    _render_authority_tiering,
    _slice_for_stream,
    _validate_conversational_text,
    sessions_in_play_from_tool_results,
)
from app.services.coach.screen_context import (
    ScreenView,
    resolve_screen_view,
    sessions_in_play_from_view,
)
from app.services.coach.turn import TurnKind, build_client, over_budget, relationship_for_user
from app.services.coach.memory_store import get_memory
from app.services.coach.prompts import render_voice_block
from app.services.coach.coaching_skills import LOAD_SKILL_TOOL, render_catalogue
from app.services.coach.proposed_actions import thread_tools
from app.services.coach.query_tools import CHAT_TOOLS
from app.services.coach.voice import resolve_voice
from app.services.readiness import build_readiness

logger = logging.getLogger(__name__)

# The LLM input is bounded to the most recent turns; the runner's scrollback is
# not (GET returns everything). 40 turns ≈ 20 exchanges, far past the point
# where older turns still steer a reply, and it caps the cost of a long-lived
# thread without deleting anything.
_MAX_LLM_HISTORY_TURNS = 40

# Cross-THREAD continuity: a bounded digest of the runner's recent turns from
# their OTHER threads (the thread sibling of chat's cross-activity digest, and
# per ADR 0027 the mechanism that makes a new thread a topic boundary, not a
# memory boundary). Same bounds as the cross-activity digest (~500 tokens).
_MAX_CROSS_THREAD_TURNS = 8
_MAX_CROSS_THREAD_CHARS = 240
_CROSS_THREAD_SCAN_LIMIT = 60

# The spend-cap answer says what still works, not silence (design spec: degrade
# with direction).
THREAD_BUDGET_PAUSED_MESSAGE = (
    "You've used this period's coaching allowance, so I can't write a reply "
    "right now. Everything else keeps running: your runs still sync, your "
    "analysis still updates, and your reports still arrive after each session. "
    "Chat comes back when the allowance resets."
)

THREAD_SYSTEM_TEMPLATE = """You are a running coach in an ongoing conversation with this runner — the SAME coach who writes their post-run analyses and remembers them between runs. This conversation is anchored to the runner and to now, not to a single run: they may ask about anything in their training, their body of work, or their plans. You coach this particular runner, not the average one: their build, their history, and what they've told you shape your advice, and what's right for a typical runner can be wrong for this one.

THE RUNNER:
{profile_json}
{baseline_block}{anchor_block}{voice_block}{cross_thread_block}{looking_at_block}{skills_block}
YOUR TOOLS — LOOKING UP THEIR TRAINING:
What is above is a lean baseline, not their record. When a question turns on data that is not already in front of you — a specific run, how something has trended, how much they have trained — LOOK IT UP with your tools instead of asking the runner for it. You have their whole training record:
- list_activities_in_range: their past sessions (any activity type, newest first) over a named window, each with distance, pace, effort, and shape.
- get_session_detail: one past session's full detail by its activity_id (pace, HR, cadence, HR drift, and whether its intervals came from the runner's own recorded laps).
- get_training_summary: computed totals, a by-type breakdown, and a vs-typical read over a named window.
Pick the window whose NAME matches how the runner spoke; never work out dates yourself — the tools resolve and report the exact range they used, so ground your answer in that. Only tell the runner you cannot answer once the tools have come up empty too.

OFFERING AN ACTION — THE RUNNER'S CALL:
When the conversation itself settles a small correction to their record — how a session felt, what a session actually was, or a session grouped wrongly with its neighbours — offer to make it with offer_proposed_action rather than sending them off to tap through the app. Offer only what the conversation reached, with ids already in front of you, and at most one per reply.
The card states the change and carries its own button, so let it speak for itself: spend your reply coaching them, and never describe the card, narrate the tap, or report the change as made. Nothing is written unless they confirm it. Write "That was a tempo by any read — 6:00/km at 150bpm is not an easy-day effort.", not "I've put a card up — confirm it and I'll update your record."

RULES:
1. Ground every claim in the data above and anything you fetch with your tools, citing the specific numbers (pace, HR, effort score) when they carry the point. Never invent facts.
2. NEVER diagnose injuries or medical conditions. If asked about pain, recommend professional assessment.
3. ZONE LANGUAGE: Unless data in front of you confirms this runner's HR zones are calibrated, never reference specific HR zones (Z1-Z5). Use effort descriptions instead (easy, moderate, hard).
4. Be concise and conversational, a knowledgeable coach rather than a chatbot. Most answers should be 2-4 sentences unless the athlete asks for detail.
5. When discussing training recommendations, be conservative. Never recommend risky volume jumps.
6. FORMATTING: Your responses will be rendered as markdown. Use proper formatting:
    - Use **bold** for emphasis (ensure both opening and closing **).
    - Use bullet points (- item) or numbered lists for multiple points.
    - Put a blank line between paragraphs and before/after lists.
    - Use ### for section headings when covering multiple topics.
    - Never run multiple topics into a single paragraph. Break them up.
    - Keep each bullet or paragraph focused on one idea.
7. WEEKLY FRAME: When the runner is talking about their week — a target, a plan, "this week" — answer in the calendar-week frame they use (the tools' calendar windows are that frame), and read a partial week as on-pace ("18k in, weekend to go"), never as a shortfall against a full-week norm. This is the same mirror-their-frame rule as picking the tool window whose name matches how they spoke.{tiering_block}"""


def _profile_dict(profile: Optional[UserProfile]) -> dict:
    if not profile:
        return {}
    out = {
        "goal_type": profile.goal_type,
        "experience_level": profile.experience_level,
        "weekly_days_available": profile.weekly_days_available,
        "current_weekly_km": profile.current_weekly_km,
        "max_hr": profile.max_hr,
        "max_hr_source": getattr(profile, "max_hr_source", None),
        "injury_notes": profile.injury_notes,
    }
    # The runner's stated build (#742): stated facts only, absent means absent —
    # never invite the coach to fill the gap with a typical runner.
    body = {}
    if getattr(profile, "weight_kg", None) is not None:
        body["weight_kg"] = profile.weight_kg
    if getattr(profile, "height_cm", None) is not None:
        body["height_cm"] = profile.height_cm
    if body:
        out["body"] = body
    return out


def _build_baseline_sections(db: Session, user: User) -> dict:
    """The relationship baseline's optional sections, keyed like the pack so the
    shared authority-tiering renderer gates its briefings on presence."""
    sections: dict = {}
    memory_row = get_memory(db, user.id)
    profile_json = getattr(memory_row, "profile", None) if memory_row else None
    if profile_json:
        sections["memory"] = profile_json
    try:
        from datetime import date

        readiness = build_readiness(db, user.id, date.today())
    except Exception:  # noqa: BLE001 — a readiness hiccup never blocks a reply
        readiness = None
    if readiness is not None:
        sections["readiness"] = {
            "as_of": str(readiness.as_of),
            "fitness": round(readiness.fitness, 1),
            "fatigue": round(readiness.fatigue, 1),
            "form": round(readiness.form, 1),
            "condition": readiness.condition,
            "trend": readiness.trend,
            "warming_up": readiness.warming_up,
        }
    return sections


def _render_baseline_block(sections: dict) -> str:
    parts = []
    if sections.get("memory"):
        parts.append(
            "\nMEMORY — what this runner has told you (their memory profile):\n"
            + json.dumps(sections["memory"], default=str)
        )
    if sections.get("readiness"):
        parts.append(
            "\nCURRENT CONDITION (deterministic readiness read):\n"
            + json.dumps(sections["readiness"], default=str)
        )
    return "\n".join(parts) + ("\n" if parts else "")


def _render_anchor_block(thread: Thread) -> str:
    """A compact note about the anchored activity. The anchor is a framing hint,
    never a data boundary (ADR 0027): detail comes through the tools."""
    activity = thread.activity if thread.activity_id else None
    if activity is None:
        return ""
    summary = {
        "activity_id": str(activity.id),
        "name": activity.name,
        "type": activity.type,
        "start_date": str(activity.start_date),
        "distance_m": activity.distance_m,
        "moving_time_s": activity.moving_time_s,
    }
    return (
        "\nTHIS CONVERSATION STARTED ON THIS SESSION (fetch its detail with "
        "get_session_detail if the conversation needs it):\n"
        + json.dumps(summary, default=str)
        + "\n"
    )


def _build_cross_thread_block(db: Session, thread: Thread, user_id) -> str:
    """A bounded digest of the runner's recent turns from their OTHER threads —
    the continuity that makes a new thread a topic boundary, not a memory
    boundary (ADR 0027). Skips error/redirect sentinels; "" when quiet."""
    rows = (
        db.query(CoachChatMessage, Thread.title)
        .join(Thread, CoachChatMessage.thread_id == Thread.id)
        .filter(
            Thread.user_id == user_id,
            CoachChatMessage.thread_id != thread.id,
        )
        .order_by(CoachChatMessage.created_at.desc(), CoachChatMessage.id.desc())
        .limit(_CROSS_THREAD_SCAN_LIMIT)
        .all()
    )
    from app.services.coach.chat import _is_noise_message

    picked = []
    for msg, _title in rows:
        if _is_noise_message(msg):
            continue
        picked.append(msg)
        if len(picked) >= _MAX_CROSS_THREAD_TURNS:
            break
    if not picked:
        return ""
    lines = []
    for msg in reversed(picked):  # chronological
        who = "Runner" if msg.role == "user" else "You"
        text = " ".join(msg.content.split())
        if len(text) > _MAX_CROSS_THREAD_CHARS:
            text = text[:_MAX_CROSS_THREAD_CHARS].rstrip() + "…"
        lines.append(f"{who}: {text}")
    return (
        "\nRELATIONSHIP CONVERSATION — recent turns from this runner's other "
        "conversations with you (continuity only):\n" + "\n".join(lines) + "\n"
    )


def _resolve_voice_block_for_user(db: Session, user_id) -> str:
    """The runner's declared voice, rendered for this turn.

    The relationship read goes through the envelope's single gated owner (#801),
    so COACH_RELATIONSHIP_ENABLED now applies here exactly as it does to the
    report. It did not before (#791): this function had its own ungated query, so
    with the switch off the report spoke in the default moderate voice while the
    conversation still spoke in the runner's declared one."""
    voice = resolve_voice(relationship_for_user(db, user_id))
    return render_voice_block(settings.COACH_PROMPT_ID, voice)


def _render_looking_at_block(screen_view: Optional[ScreenView]) -> str:
    """The one live screen view (#767, ADR 0028): the CURRENT screen only, never
    an accumulation — past turns keep their label, not their view. The view is
    the same data the pack's sections already carry, so it inherits their place
    in the authority tiering: citable, never overriding measured data or the
    safety floor."""
    if screen_view is None:
        return ""
    lines = [
        "\nLOOKING AT — the screen the runner is on right now (server-resolved; "
        "their view selections, your recomputed numbers):",
        f"Screen: {screen_view.label}",
    ]
    if screen_view.view is not None:
        lines.append(json.dumps(screen_view.view, default=str))
    lines.append(
        'When the runner says "this" or "here", they usually mean this screen. '
        "It is the same data as your other context: citable, never overriding "
        "measured data or the safety floor.\n"
    )
    return "\n".join(lines)


def build_thread_system_prompt(
    db: Session,
    user: User,
    thread: Thread,
    screen_view: Optional[ScreenView] = None,
) -> str:
    profile = (
        db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    )
    sections = _build_baseline_sections(db, user)
    voice_block = _resolve_voice_block_for_user(db, user.id)
    cross_thread_block = _build_cross_thread_block(db, thread, user.id)
    tiering_block = _render_authority_tiering(
        sections,
        voice_present=bool(voice_block),
        conversation_present=bool(cross_thread_block),
    )
    return THREAD_SYSTEM_TEMPLATE.format(
        profile_json=json.dumps(_profile_dict(profile), default=str),
        baseline_block=_render_baseline_block(sections),
        anchor_block=_render_anchor_block(thread),
        voice_block=voice_block,
        cross_thread_block=cross_thread_block,
        looking_at_block=_render_looking_at_block(screen_view),
        skills_block=render_catalogue(),
        tiering_block=tiering_block,
    )


async def stream_thread_turn(
    db: Session,
    user: User,
    *,
    message: str,
    thread: Optional[Thread] = None,
    anchor_activity: Optional[Activity] = None,
    asked_from: Optional[str] = None,
    screen: Optional[ScreenPointer] = None,
) -> AsyncIterator[ChatStreamEvent]:
    """One thread turn: create/continue the thread, stream the coach's reply.

    The caller has already owner-verified `thread` / `anchor_activity`. The
    `screen` pointer (#767) resolves server-side, owner-scoped; when present it
    also supplies the turn's provenance label.
    """
    # Spend cap first, before any row is written: the decline is an answer, not
    # a turn. The client built below is metered, so what this gate observes is
    # what conversational turns actually spend (#786) — before, the gate only
    # ever tripped on spend that OTHER paths had recorded.
    if over_budget(user.id):
        yield ChatStreamEvent(text=THREAD_BUDGET_PAUSED_MESSAGE)
        return

    created = False
    if thread is None:
        thread = Thread(
            user_id=user.id,
            activity_id=anchor_activity.id if anchor_activity else None,
        )
        db.add(thread)
        db.commit()
        db.refresh(thread)
        created = True

    # The pointer supplies the provenance label (past turns keep the label of
    # where they were asked, never the resolved view — ADR 0028).
    if screen is not None:
        asked_from = screen.screen

    user_msg = CoachChatMessage(
        thread_id=thread.id,
        activity_id=thread.activity_id,
        role="user",
        content=message,
        asked_from=asked_from,
    )
    db.add(user_msg)
    thread_service.touch_thread(db, thread)
    db.commit()

    # Announce the thread so a client that started a new one learns its id —
    # after the user message persists, so the announced title already resolves
    # to something real rather than the "New thread" placeholder.
    yield ChatStreamEvent(
        thread_meta={
            "thread_id": str(thread.id),
            "created": created,
            "title": thread_service.display_title(db, thread),
        }
    )

    screen_view = (
        resolve_screen_view(db, user.id, screen) if screen is not None else None
    )
    system_prompt = build_thread_system_prompt(db, user, thread, screen_view)

    history = thread_service.thread_messages(db, thread)[-_MAX_LLM_HISTORY_TURNS:]
    llm_messages = [{"role": row.role, "content": row.content} for row in history]

    client = build_client(TurnKind.THREAD, user.id)

    out: dict = {}
    async for event in _buffered_tool_loop(
        db,
        client,
        system_prompt=system_prompt,
        llm_messages=llm_messages,
        owner_user_id=user.id,
        out=out,
        tools=[*thread_tools(CHAT_TOOLS), LOAD_SKILL_TOOL],
    ):
        yield event

    if out["stream_failed"]:
        assistant_text = out["stream_fail_message"]
    else:
        assistant_text = out["assistant_text"] or ""
        # The deterministic floor, re-sourced from the TURN'S OWN FACTS (#767,
        # ADR 0028): medical scope unconditional; zone language from the
        # runner's profile calibration; interval-execution claims gated against
        # the sessions actually fetched or shown this turn.
        from app.services.coach.context import zones_calibration

        profile_row = (
            db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        )
        zones_calibrated, _basis = zones_calibration(profile_row)
        sessions_in_play = sessions_in_play_from_tool_results(
            out.get("tool_results", [])
        ) + sessions_in_play_from_view(screen_view)
        violations = _validate_conversational_text(
            assistant_text,
            zones_calibrated=zones_calibrated,
            sessions_in_play=sessions_in_play,
        )
        if any(v.rule == "medical_overreach" for v in violations):
            logger.warning(
                "thread reply gated for medical overreach (thread %s): %s",
                thread.id,
                [v.rule for v in violations],
            )
            assistant_text = MEDICAL_REDIRECT_MESSAGE
        elif violations:
            logger.warning(
                "thread reply has tolerated policy violations (thread %s): %s",
                thread.id,
                [v.rule for v in violations],
            )

    for piece in _slice_for_stream(assistant_text):
        yield ChatStreamEvent(text=piece)

    if out.get("proposed_action") is not None:
        yield ChatStreamEvent(proposed_action=out["proposed_action"])

    assistant_msg = CoachChatMessage(
        thread_id=thread.id,
        activity_id=thread.activity_id,
        role="assistant",
        content=assistant_text,
        tools_used=out["tool_trace"] or None,
        skills_used=out.get("skills_used") or None,
        asked_from=asked_from,
    )
    db.add(assistant_msg)
    thread_service.touch_thread(db, thread)
    db.commit()

    _after_assistant_reply(db, thread)


def _after_assistant_reply(db: Session, thread: Thread) -> None:
    """Post-turn side effects, both fail-soft: the title write (first exchange)
    and the thread-quiet memory debounce. Neither may break the stream."""
    try:
        from app.jobs.thread_maintenance import (
            enqueue_title_generation,
            schedule_thread_quiet_check,
        )

        enqueue_title_generation(db, thread)
        schedule_thread_quiet_check(db, thread)
    except Exception:  # noqa: BLE001 — background niceties never break a reply
        logger.exception("thread post-turn scheduling failed for %s", thread.id)
