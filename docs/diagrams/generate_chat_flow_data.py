#!/usr/bin/env python3
"""Regenerate the CHAT blob in coach-chat-nodes.js (the coach-chat-flow-graph.html
data-flow diagram) from the REAL code-resident artifacts.

The sibling generator (generate_flow_nodes_data.py) captures one activity's context
pack from the seeded DB, because the report pack IS per-run data. A thread turn is
different: its context is assembled per-turn from a lean relationship baseline, and
almost everything the diagram is about — the system-prompt template and its slots,
the tool JSON schemas, the coaching-skill procedures, the pointer contract, the
policy floor's messages, the loop bounds — is CODE-RESIDENT. So this generator
imports the backend and dumps those artifacts verbatim. No DB is required.

Optionally, with a local seeded DB reachable, it also assembles ONE REAL system
prompt through `build_thread_system_prompt` for a real user + thread, so the
diagram can show the assembled article rather than only the template. That capture
is best-effort: without a DB the diagram still renders, marked as template-only.

Usage:
    python docs/diagrams/generate_chat_flow_data.py              # code artifacts (+ DB capture if reachable)
    CHAT_NO_DB=1 python docs/diagrams/generate_chat_flow_data.py # skip the DB capture entirely

Read-only against the DB; rewrites only the generated line of coach-chat-nodes.js.
Local DB only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from chat_capture_plan import (
    BACKEND,
    TARGET,
    CaptureRefused,
    _CAPTURE_KEYS,
    _committed_capture,
    _db_migration_gap,
    _migration_gap,
    _screen_resolution_summary,
    plan_capture,
)

sys.path.insert(0, str(BACKEND))

from app.core.config import settings  # noqa: E402
from app.schemas.thread import ScreenPointer, ThreadMessageSend  # noqa: E402
from app.services.coach import chat as chat_mod  # noqa: E402
from app.services.coach import coaching_skills, event_search, proposed_actions, query_tools  # noqa: E402
from app.services.coach import thread_turn as tt  # noqa: E402
from app.jobs import thread_maintenance as tm  # noqa: E402
from app.services import intents  # noqa: E402

from chat_capture_tools import (  # noqa: E402
    _fill_violations,
    _run_recorded_calls,
    _run_tools,
    _turn_facts,
)

# The capture is pinned to PRODUCTION config by default, so the diagram is a
# one-to-one picture of what the coach actually receives in prod rather than of
# whatever the developer's backend/.env happens to hold. (A local .env commonly
# carries the #522 kill switches flipped off and an older prompt id, which would
# render the voice slot absent for a config reason and misrepresent the system.)
# `CHAT_CAPTURE_CONFIG=local` captures under the local .env as-is instead.
# Verified against Railway 2026-08-12. A literal here goes stale silently, which
# is the failure #841 fixed in the sibling generator — so when this diverges from
# the live prompt, the capture is of a configuration production does not run.
CAPTURE_CONFIG = os.environ.get("CHAT_CAPTURE_CONFIG", "prod")


_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / "backend" / ".env.example"


def _prod_prompt_id() -> str:
    """The prod prompt id, read from .env.example -- never a literal.

    This was a hardcoded default, and it rotted the moment prod moved: a run
    with the correct id in the environment was silently forced BACK to the
    stale literal, producing a capture of a prompt production had stopped
    running, with the meta cheerfully reporting `prod-pinned`. The sibling
    generator carries the same warning in prose for the same reason -- its id
    "sat at grouped_v5 while prod ran grouped_v9".

    Read from the same file `_prod_flag_contract` already reads and the drift
    guard already checks, so pinning here and checking there cannot disagree.
    `PROMPT_ID` still overrides, for capturing a version that is not yet live.
    """
    override = os.environ.get("PROMPT_ID")
    if override:
        return override
    if _ENV_EXAMPLE.is_file():
        found = re.search(
            r"^COACH_PROMPT_ID=([A-Za-z0-9_]+)", _ENV_EXAMPLE.read_text(), re.M
        )
        if found:
            return found.group(1)
    raise RuntimeError(
        "no prod COACH_PROMPT_ID: backend/.env.example is missing or does not "
        "declare one, and capturing under a guessed prompt is how this file "
        "went stale before. Set PROMPT_ID explicitly if that is intended."
    )


PROD_PROMPT_ID = _prod_prompt_id()


def _prod_flag_contract() -> dict:
    """The prod-parity value of every COACH_*_ENABLED flag, from .env.example.

    Deliberately NOT the code defaults, which is what #793 suggested. Every
    COACH_*_ENABLED default is True, while production runs most of the #522 kill
    switches OFF (.env.example line 100 onward). Pinning to code defaults would
    make the label honest about being pinned and dishonest about what it was
    pinned TO -- a capture of a configuration production does not run, which is
    exactly the failure #841 found in the sibling generator and corrected there
    by making .env.example the checked contract. The drift guard reads the same
    file, so pinning here and checking there cannot disagree.
    """
    if not _ENV_EXAMPLE.is_file():
        return {}
    text = _ENV_EXAMPLE.read_text()
    return {
        k: v == "true"
        for k, v in re.findall(r"^(COACH_[A-Z0-9_]+_ENABLED)=(true|false)", text, re.M)
    }


def _coach_flag_names() -> list[str]:
    return sorted(
        n for n in type(settings).model_fields
        if n.startswith("COACH_") and n.endswith("_ENABLED")
    )


def _pin_config() -> dict:
    """Pin every setting the capture depends on. Returns what was forced, and the
    full resolved map, so `prod-pinned` is a claim the meta can be checked against.

    Before #793 this pinned the prompt id and THREE flags out of seventeen, and
    the other fourteen rode whatever the developer's backend/.env held. The
    sharpest case was COACH_RELATIONSHIP_ENABLED: the runner's voice reaches the
    prompt only when it AND COACH_VOICE_BLOCK_ENABLED are on, so forcing the
    voice flag alone did not guarantee the voice slot rendered at all. Same
    reasoning as #752, which took `make backend-test` off the local env file: a
    diagram of what the coach receives should not depend on one machine's .env.
    """
    if CAPTURE_CONFIG == "local":
        return {"mode": "local .env", "forced": {}, "flags": {}, "contract": None}
    forced = {}
    if settings.COACH_PROMPT_ID != PROD_PROMPT_ID:
        forced["COACH_PROMPT_ID"] = f"{settings.COACH_PROMPT_ID} → {PROD_PROMPT_ID}"
        settings.COACH_PROMPT_ID = PROD_PROMPT_ID

    contract = _prod_flag_contract()
    flags = {}
    for flag in _coach_flag_names():
        # A flag .env.example does not declare falls back to the code default,
        # which is what production resolves for it too.
        want = contract.get(flag, type(settings).model_fields[flag].default)
        have = getattr(settings, flag)
        if have != want:
            forced[flag] = f"{have} → {want}"
            setattr(settings, flag, want)
        flags[flag] = want
    return {
        "mode": "prod-pinned",
        "forced": forced,
        "flags": flags,
        "contract": "backend/.env.example",
    }


def _guard_local() -> None:
    url = settings.DATABASE_URL or ""
    if url and "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"refusing non-local DB target: {url!r}")



# How many of the runner's most recent conversations the diagram offers in its
# selector, and how much of each is carried. Bounded so the generated file stays a
# reasonable size — a full assembled prompt is ~10k characters each.
_MAX_THREADS = 8
_MAX_TURNS_PER_THREAD = 24
_MAX_REPLY_CHARS = 2600


# Screens this generator can rebuild a pointer for. Coverage is a DECLARATION
# rather than something a reader has to infer from the branches below, because
# `check_diagram_drift` compares it against `ScreenPointer`'s own screen Literal:
# a screen the schema accepts and this file does not name draws every turn asked
# from it as having NO screen context at all, which is what happened to
# `schedule` between the enum gaining it and this being noticed. The two tuples
# are split because they carry different obligations — an identity-only screen
# needs only its name here, a rebuilt one needs a branch as well.
_REBUILT_SCREENS = ("activity", "trends")
_IDENTITY_ONLY_SCREENS = ("home", "activities", "load", "profile", "schedule")
SCREENS_COVERED = ("activity", "trends", "home", "activities", "load", "profile", "schedule")


def _pointer_for(asked_from, thread):
    """Rebuild the screen pointer a turn was asked under, from its stored label.

    `asked_from` is the only screen provenance persisted (#767 deliberately stores
    the LABEL, never the resolved view), so an activity turn recovers its id from
    the thread anchor and a trends turn falls back to the resolver's own default
    range. The runner's actual range/type selections are NOT recoverable — they
    were inputs to that turn, not stored facts.

    Returns `(pointer, caveat)`. A screen named in `SCREENS_COVERED` that still
    yields no pointer returns a caveat saying so, so the gap shows up IN the
    capture rather than as an indistinguishable `screen: null` — the state a turn
    asked from no screen at all legitimately has.
    """
    if asked_from == "activity" and thread.activity_id is not None:
        return ScreenPointer(screen="activity", activity_id=thread.activity_id), None
    if asked_from == "trends":
        return ScreenPointer(screen="trends"), (
            "the runner's actual range/type selections are not stored, so this "
            "resolves at the builder's default range"
        )
    if asked_from in _IDENTITY_ONLY_SCREENS:
        return ScreenPointer(screen=asked_from), None
    if asked_from in SCREENS_COVERED:
        return None, (
            f"{asked_from!r} is a screen this capture claims to cover but could not "
            "rebuild a pointer for, so its context is missing rather than absent"
        )
    return None, None


def _capture_thread(db, user, thread, chat_mod_ref) -> dict:
    """One conversation: how its context is built, and what each turn used."""
    from app.models.coach_chat_message import CoachChatMessage
    from app.models.user_profile import UserProfile
    from app.services.coach import screen_context as screen_ctx
    from app.services.coach import threads as thread_service
    from app.services.coach.screen_context import resolve_screen_view

    rows = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread.id)
        .order_by(CoachChatMessage.created_at.asc(), CoachChatMessage.id.asc())
        .all()
    )
    first_user = next((r for r in rows if r.role == "user"), None)

    # --- the pack as it is built for THIS conversation ---
    # The conversation-level screen is the FIRST user message's, kept because the
    # thread/entry nodes are conversation-scoped. Everything downstream of the
    # pointer is now resolved PER TURN instead (#792): `stream_thread_turn`
    # resolves `screen` on every call, so a runner who asks from Trends and then
    # from an activity page gets two different LOOKING AT blocks, and showing the
    # first one twice is a claim the data cannot back.
    pointer, pointer_caveat = _pointer_for(
        first_user.asked_from if first_user else None, thread
    )
    screen_view = None
    if pointer is not None:
        screen_view = resolve_screen_view(db, user.id, pointer)

    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    sections = tt._build_baseline_sections(db, user)
    voice_block = tt._resolve_voice_block_for_user(db, user.id)
    cross_block = tt._build_cross_thread_block(db, thread, user.id)
    anchor_block = tt._render_anchor_block(thread)
    looking_block = tt._render_looking_at_block(screen_view)
    tiering_block = chat_mod_ref._render_authority_tiering(
        sections,
        voice_present=bool(voice_block),
        conversation_present=bool(cross_block),
    )
    prompt = tt.build_thread_system_prompt(db, user, thread, screen_view)

    # The floor's zone rule reads the runner's own calibration, not a constant.
    from app.services.coach.context import zones_calibration

    zones_calibrated, zones_basis = zones_calibration(profile)
    # The screen view contributes its session to rule 4 exactly as it does live.
    view_sessions = screen_ctx.sessions_in_play_from_view(screen_view)

    # --- per-turn: what the coach reached for, and what it was LOOKING AT ---
    # Resolving a screen and re-assembling the prompt is memoised on the screen
    # label, because those are the only inputs that vary within a conversation;
    # a thread whose every turn came from one screen therefore costs exactly what
    # it cost before.
    per_screen: dict = {}

    def _screen_capture(asked_from):
        if asked_from in per_screen:
            return per_screen[asked_from]
        ptr, caveat = _pointer_for(asked_from, thread)
        view = resolve_screen_view(db, user.id, ptr) if ptr is not None else None
        cap = {
            "screen": ptr.screen if ptr is not None else None,
            "caveat": caveat,
            "looking_at_block": tt._render_looking_at_block(view),
            "screen_view": (
                {"label": view.label, "view": view.view} if view else None
            ),
            "view_sessions": screen_ctx.sessions_in_play_from_view(view),
            "prompt": tt.build_thread_system_prompt(db, user, thread, view),
        }
        per_screen[asked_from] = cap
        return cap

    turns = []
    assistant_seen = 0
    pending_asked_from = None
    for i, r in enumerate(rows[:_MAX_TURNS_PER_THREAD]):
        tools = r.tools_used if isinstance(r.tools_used, list) else []
        skills = r.skills_used if isinstance(r.skills_used, list) else []
        if r.role == "user":
            pending_asked_from = r.asked_from
        # A generation belongs to the screen the RUNNER's message was asked
        # from; the assistant row carries no `asked_from` of its own.
        turn_screen = _screen_capture(
            pending_asked_from if r.role == "assistant" else r.asked_from
        )
        if r.role == "assistant":
            assistant_seen += 1
        turns.append({
            "i": i,
            "role": r.role,
            "asked_from": r.asked_from,
            # --- #792: per-turn, not inherited from the first user message ---
            "screen": turn_screen["screen"],
            "pointer_caveat": turn_screen["caveat"],
            "looking_at_block": turn_screen["looking_at_block"],
            "screen_view": turn_screen["screen_view"],
            "view_sessions": turn_screen["view_sessions"],
            # The assembled prompt is ~13k chars, so it is carried per turn ONLY
            # when this turn's screen actually produced a different one. A turn
            # storing null reads the conversation-level prompt, which for that
            # turn IS what was assembled. Measured: carrying it unconditionally
            # roughly doubled the file.
            "prompt": (
                turn_screen["prompt"]
                if turn_screen["prompt"] != prompt else None
            ),
            "prompt_chars": len(turn_screen["prompt"]),
            # `enqueue_title_generation` returns early unless the thread is
            # untitled AND this is the FIRST assistant reply, so every later turn
            # showed a title job that did not run.
            "assistant_index": (assistant_seen - 1) if r.role == "assistant" else None,
            # `schedule_thread_quiet_check` is passed the count AT THAT MOMENT;
            # the node was using the conversation total for every turn.
            "messages_at_turn": i + 1,
            "at": str(r.created_at),
            "content": (r.content or "")[:_MAX_REPLY_CHARS],
            "truncated": len(r.content or "") > _MAX_REPLY_CHARS,
            "chars": len(r.content or ""),
            "tools_used": tools,
            "skills_used": skills,
            # the redirect is byte-identical to the canned message when rule 5 fired
            "was_redirected": (r.content or "").strip() == chat_mod_ref.MEDICAL_REDIRECT_MESSAGE,
            # filled by _fill_violations once the re-run tool results exist; it
            # validates the FULL reply, not the display-truncated copy
            "violations": None,
            "sessions_in_play": 0,
            "_full": r.content or "",
        })

    tool_calls = sum(len(t["tools_used"]) for t in turns)
    skill_loads = sum(len(t["skills_used"]) for t in turns)
    return {
        "id": str(thread.id),
        "title": thread.title,
        "display_title": thread_service.display_title(db, thread),
        # the Thread row as stored, for the table node
        "thread_row": {
            "id": str(thread.id),
            "user_id": str(thread.user_id),
            "activity_id": str(thread.activity_id) if thread.activity_id else None,
            "title": thread.title,
            "created_at": str(thread.created_at),
            "last_message_at": str(thread.last_message_at) if thread.last_message_at else None,
        },
        "anchored": thread.activity_id is not None,
        "created_at": str(thread.created_at),
        "last_message_at": str(thread.last_message_at) if thread.last_message_at else None,
        # what the policy floor was gated against for this conversation
        "floor": {
            "zones_calibrated": zones_calibrated,
            "zones_basis": zones_basis,
            "view_sessions": view_sessions,
        },
        "started_from": first_user.asked_from if first_user else None,
        "turn_count": len(rows),
        "tool_calls": tool_calls,
        "skill_loads": skill_loads,
        "pointer_caveat": pointer_caveat,
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "present": {
            "memory": bool(sections.get("memory")),
            "readiness": bool(sections.get("readiness")),
            "anchor": bool(anchor_block),
            "voice": bool(voice_block),
            "cross_thread": bool(cross_block),
            "looking_at": bool(looking_block),
        },
        "builders": {
            "profile": tt._profile_dict(profile),
            "memory": sections.get("memory"),
            "readiness": sections.get("readiness"),
            # #856, as in _capture_assembled below: present as a key whenever the
            # coach was given the schedule, None inside it when the runner has no
            # plan. It was missing here while the turn-level capture had it, so a
            # selected conversation showed no schedule at all.
            "schedule": sections.get("schedule"),
            # #859, as in _capture_assembled above: the running norm and the
            # ceiling a settled block is judged against.
            "running_norm": sections.get("running_norm"),
            "anchor_block": anchor_block,
            "voice_block": voice_block,
            "cross_thread_block": cross_block,
            "looking_at_block": looking_block,
            "tiering_block": tiering_block,
            "screen_view": (
                {"label": screen_view.label, "view": screen_view.view}
                if screen_view else None
            ),
        },
        "turns": turns,
        "turns_truncated": len(rows) > _MAX_TURNS_PER_THREAD,
    }


def _capture_assembled() -> dict:
    """Capture ONE real turn end to end: every builder's real output, the real
    assembled prompt, the real tool results, and the real stored reply.

    Everything here is produced by the REAL code against the seeded local DB —
    no model call is made, because the turn's reply is already stored and the
    data tools are deterministic reads. Best-effort: any failure degrades the
    diagram to template-only rather than breaking it.
    """
    if os.environ.get("CHAT_NO_DB"):
        return {"ok": False, "reason": "CHAT_NO_DB set"}
    try:
        _guard_local()
        from app.db.session import SessionLocal
        from app.models.coach_chat_message import CoachChatMessage
        from app.models.thread import Thread
        from app.models.user import User
        from app.models.user_profile import UserProfile
        from app.services.coach import threads as thread_service
        from app.services.coach.memory_store import get_memory
        from app.services.coach.screen_context import resolve_screen_view

        db = SessionLocal()
        try:
            behind = _db_migration_gap(db)
            if behind:
                return {"ok": False, "reason": behind, "behind_migrations": True}
            # The runner to trace: the one with the most conversation, i.e. the
            # deployment owner on a seeded snapshot. Picking by "richest single
            # turn" instead would land on whichever user happened to have one good
            # turn, and their thread list — what the selector offers — would be a
            # single conversation.
            from sqlalchemy import func

            owner = (
                db.query(Thread.user_id, func.count(CoachChatMessage.id).label("n"))
                .join(CoachChatMessage, CoachChatMessage.thread_id == Thread.id)
                .group_by(Thread.user_id)
                .order_by(func.count(CoachChatMessage.id).desc())
                .first()
            )
            if owner is None:
                return {"ok": False, "reason": "no conversations in the local DB"}
            user = db.query(User).filter(User.id == owner[0]).first()
            # Within that runner, the turn the single-turn nodes trace: their most
            # recent conversation's last assistant turn.
            thread = (
                db.query(Thread)
                .filter(Thread.user_id == user.id)
                .order_by(Thread.last_message_at.desc().nullslast())
                .first()
            )
            row = (
                db.query(CoachChatMessage)
                .filter(
                    CoachChatMessage.thread_id == thread.id,
                    CoachChatMessage.role == "assistant",
                )
                .order_by(CoachChatMessage.created_at.desc())
                .first()
            )
            if row is None:
                return {"ok": False, "reason": "newest conversation has no assistant turn"}

            # --- the real screen view, resolved through the real resolver ---
            screen_view = None
            if thread.activity_id is not None:
                screen_view = resolve_screen_view(
                    db, user.id,
                    ScreenPointer(screen="activity", activity_id=thread.activity_id),
                )

            # --- every builder's REAL output, one by one ---
            profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
            sections = tt._build_baseline_sections(db, user)
            voice_block = tt._resolve_voice_block_for_user(db, user.id)
            cross_block = tt._build_cross_thread_block(db, thread, user.id)
            anchor_block = tt._render_anchor_block(thread)
            looking_block = tt._render_looking_at_block(screen_view)
            tiering_block = chat_mod._render_authority_tiering(
                sections,
                voice_present=bool(voice_block),
                conversation_present=bool(cross_block),
            )
            prompt = tt.build_thread_system_prompt(db, user, thread, screen_view)

            builders = {
                "profile": tt._profile_dict(profile),
                "memory": sections.get("memory"),
                "readiness": sections.get("readiness"),
                # #856. Present as a key whenever the coach was given the
                # schedule; None inside it means the runner has no plan, which is
                # itself what the coach is told. Absent only when
                # COACH_SCHEDULE_ENABLED is off.
                "schedule": sections.get("schedule"),
                # #859. The runner's own typical running week and the ceiling it
                # implies — the number a block settled in this conversation will
                # be measured against when it is written. Same key presence rule
                # as the schedule above: absent only when the switch is off.
                "running_norm": sections.get("running_norm"),
                "anchor_block": anchor_block,
                "voice_block": voice_block,
                "cross_thread_block": cross_block,
                "looking_at_block": looking_block,
                "tiering_block": tiering_block,
                "screen_view": (
                    {"label": screen_view.label, "view": screen_view.view}
                    if screen_view else None
                ),
            }

            # --- the bounded history the model is actually handed ---
            # Filtered as `thread_turn._llm_history` filters it (#1124): an event
            # row, a notice or a failed turn is never handed to the model.
            history = [
                m for m in thread_service.thread_messages(db, thread)
                if thread_service.was_said(m)
            ][-tt._MAX_LLM_HISTORY_TURNS:]
            history_facts = {
                "total_in_thread": db.query(CoachChatMessage)
                    .filter(CoachChatMessage.thread_id == thread.id).count(),
                "sent_to_model": len(history),
                "turns": [
                    {"role": m.role, "asked_from": m.asked_from,
                     "content": (m.content or "")[:400]}
                    for m in history[-8:]
                ],
            }

            # --- the tools, really executed ---
            trace = row.tools_used if isinstance(row.tools_used, list) else []
            tool_results = _run_tools(db, user.id, thread, trace)

            # --- a REAL medical-redirect firing, if the data holds one ---
            redirect_row = (
                db.query(CoachChatMessage)
                .filter(
                    CoachChatMessage.role == "assistant",
                    CoachChatMessage.content == chat_mod.MEDICAL_REDIRECT_MESSAGE,
                )
                .order_by(CoachChatMessage.created_at.desc())
                .first()
            )
            redirect_example = None
            if redirect_row is not None:
                asked = (
                    db.query(CoachChatMessage)
                    .filter(
                        CoachChatMessage.thread_id == redirect_row.thread_id,
                        CoachChatMessage.role == "user",
                        CoachChatMessage.created_at <= redirect_row.created_at,
                    )
                    .order_by(CoachChatMessage.created_at.desc())
                    .first()
                )
                redirect_example = {
                    "question": asked.content if asked else None,
                    "served": redirect_row.content,
                    "at": str(redirect_row.created_at),
                }

            # --- every recent conversation, for the diagram's selector ---
            recent = (
                db.query(Thread)
                .filter(Thread.user_id == user.id)
                .order_by(Thread.last_message_at.desc().nullslast())
                .limit(_MAX_THREADS)
                .all()
            )
            conversations = []
            for th in recent:
                try:
                    conversations.append(_capture_thread(db, user, th, chat_mod))
                except Exception as exc:  # noqa: BLE001 — one bad thread never breaks the set
                    print(f"  ! thread {th.id} capture failed: {exc}", file=sys.stderr)
            # Re-run each conversation's tools over the windows ITS turns recorded,
            # so the selector's tool nodes match the selected conversation.
            for conv in conversations:
                trace = [t for turn in conv["turns"] for t in turn["tools_used"]]
                th = next(x for x in recent if str(x.id) == conv["id"])
                conv["tool_results"] = _run_tools(db, user.id, th, trace)
                conv["tool_runs"] = _run_recorded_calls(db, user.id, th, conv["turns"])
                # needs the re-run results, so it runs last
                _fill_violations(conv, chat_mod)

            corpus = db.query(Thread).filter(Thread.user_id == user.id).count()
            return {
                "ok": True,
                "conversations": conversations,
                "default_conversation": conversations[0]["id"] if conversations else None,
                "prompt": prompt,
                "chars": len(prompt),
                "present": {
                    "memory": bool(sections.get("memory")),
                    "readiness": bool(sections.get("readiness")),
                    "anchor": bool(anchor_block),
                    "voice": bool(voice_block),
                    "cross_thread": bool(cross_block),
                    "looking_at": bool(looking_block),
                },
                "anchored": thread.activity_id is not None,
                "thread_title": thread.title,
                "builders": builders,
                "turn": _turn_facts(db, row, thread),
                "history": history_facts,
                "tool_results": tool_results,
                "redirect_example": redirect_example,
                "corpus": {
                    "threads": corpus,
                    "messages": db.query(CoachChatMessage)
                        .join(Thread, CoachChatMessage.thread_id == Thread.id)
                        .filter(Thread.user_id == user.id).count(),
                },
            }
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — the diagram renders without it
        import traceback
        traceback.print_exc()
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


# --- code-resident artifacts ----------------------------------------------------

def _declared_screen_builders_for_capture() -> list[str]:
    """The screen keys that resolve a real view, read through the GUARD's own reader.

    Deliberately not a second AST walk written here. #855's rule is that a guard
    which re-implements the decision it checks is a second copy of that decision;
    the same holds for the record it checks against. One reader, two callers, so
    the only way live and recorded can differ is the one this exists to catch --
    somebody changed `resolve_screen_view` and did not regenerate the diagram.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_diagram_drift import _declared_screen_builders

    return _declared_screen_builders()


def build() -> dict:
    pinned = _pin_config()
    catalogue = coaching_skills.render_catalogue()
    procedures = "".join(s.procedure for s in coaching_skills.SKILLS)

    return {
        "meta": {
            "generated_from": "code artifacts",
            "capture_config": pinned["mode"],
            "forced": pinned["forced"],
            "coach_prompt_id": settings.COACH_PROMPT_ID,
            "chat_model_id": settings.chat_model_id,
            "threads_enabled": bool(settings.COACH_THREADS_ENABLED),
            "voice_block_enabled": bool(settings.COACH_VOICE_BLOCK_ENABLED),
            "memory_enabled": bool(settings.COACH_MEMORY_ENABLED),
            # #793: every COACH_*_ENABLED flag the capture ran under, so the
            # `prod-pinned` label is a claim that can be CHECKED rather than
            # taken on trust. Three used to be pinned and recorded; fourteen
            # rode the developer's .env unrecorded.
            "coach_flags": pinned["flags"],
            "flag_contract": pinned["contract"],
            "block_gap_seconds": settings.BLOCK_GAP_SECONDS,
        },
        # #871 follow-up: which screen keys actually RESOLVE A VIEW, as opposed
        # to the identity-only screens whose content the baseline already
        # carries. The sixth pinned surface set, and deliberately top-level
        # rather than folded into `assembled.builders.screen_view`: that is a
        # captured VALUE from one conversation, this is a DECLARATION read off
        # resolve_screen_view's own branches, and the guard's stated rule is to
        # compare a declaration against a record, never against a captured
        # value. #870 is what makes writing it safe -- the generator now refuses
        # to replace a real capture with none.
        "screen_builders": sorted(_declared_screen_builders_for_capture()),
        # the prompt template, verbatim, with its {slot} markers intact
        "template": tt.THREAD_SYSTEM_TEMPLATE,
        "slot_order": re.findall(r"\{(\w+)\}", tt.THREAD_SYSTEM_TEMPLATE),
        "assembled": _capture_assembled(),
        # the authority-tiering constants, and what each bullet says
        "tiering": {
            "header": chat_mod._TIERING_HEADER,
            "voice": chat_mod._VOICE_TIER,
            "memory": chat_mod._MEMORY_TIER,
            "corpus": chat_mod._CORPUS_TIER,
            "corpus_only": chat_mod._CORPUS_ONLY_TIER,
            "training_load": chat_mod._TRAINING_LOAD_TIER,
            "conversation": chat_mod._RELATIONSHIP_CONVERSATION_TIER,
        },
        # every tool a thread turn is given, in the order it is passed
        "tools": {
            "data": query_tools.CHAT_TOOLS,
            "action": proposed_actions.PROPOSED_ACTION_TOOL,
            # the API-run web search (#1051): present when COACH_EVENT_SEARCH_ENABLED
            "search": event_search.WEB_SEARCH_TOOL,
            "skill": coaching_skills.LOAD_SKILL_TOOL,
            "status_labels": query_tools.TOOL_STATUS_LABELS,
            "trace_labels": query_tools.TOOL_TRACE_LABELS,
            "window_trace_labels": query_tools.WINDOW_TRACE_LABELS,
            "list_windows": list(query_tools.LIST_WINDOWS),
            "summary_windows": list(query_tools.SUMMARY_WINDOWS),
            "type_filter": query_tools._TYPE_FILTER,
        },
        # the house procedures, in full, with the failure each was written for
        "skills": [
            {
                "name": s.name,
                "use_when": s.use_when,
                "procedure": s.procedure,
                "earned_by": s.earned_by,
            }
            for s in coaching_skills.SKILLS
        ],
        "skill_cost": {
            "catalogue": catalogue,
            "catalogue_chars": len(catalogue),
            "procedure_chars": len(procedures),
        },
        # the client-facing contracts
        "schemas": {
            "screen_pointer": ScreenPointer.model_json_schema(),
            "thread_send": ThreadMessageSend.model_json_schema(),
            "proposed_action": proposed_actions.ProposedActionRequest.model_json_schema(),
        },
        "intent_options": intents.INTENT_OPTIONS_BY_TYPE,
        # the floor's canned answers
        "messages": {
            "medical_redirect": chat_mod.MEDICAL_REDIRECT_MESSAGE,
            "budget_paused": tt.THREAD_BUDGET_PAUSED_MESSAGE,
            "title_system": tm._TITLE_SYSTEM,
        },
        # every bound the turn runs under
        "bounds": {
            "max_tool_rounds": chat_mod._MAX_TOOL_ROUNDS,
            "max_llm_history_turns": tt._MAX_LLM_HISTORY_TURNS,
            "max_cross_thread_turns": tt._MAX_CROSS_THREAD_TURNS,
            "max_cross_thread_chars": tt._MAX_CROSS_THREAD_CHARS,
            "cross_thread_scan_limit": tt._CROSS_THREAD_SCAN_LIMIT,
            "stream_slice_chars": chat_mod._STREAM_SLICE_CHARS,
            "heartbeat_interval_seconds": chat_mod._HEARTBEAT_INTERVAL_SECONDS,
            "action_token_ttl_seconds": proposed_actions._TOKEN_TTL_SECONDS,
            "thread_memory_cooldown_seconds": tm._THREAD_MEMORY_COOLDOWN_SECONDS,
            "title_model_id": tm._TITLE_MODEL_ID,
            "title_max_tokens": tm._TITLE_MAX_TOKENS,
        },
    }



def main() -> None:
    data = build()

    # #870: without a seeded database the capture comes back template-only, and
    # writing it would silently replace every real value in the committed
    # diagram with "no capture". The standing project rule is that a change to
    # what the coach receives regenerates the diagram in the same PR, so an
    # agent or a developer with no seeded DB could satisfy that rule
    # mechanically and leave the artifact showing LESS than it did before.
    # #855's capture-parity check catches the aftermath at the guard; this
    # refuses to produce it in the first place, one step earlier and with a
    # message that says what is missing rather than reading as a guard
    # complaint.
    #
    # Preserving beats hard-failing, and the issue's acceptance criteria allow
    # either: most regenerations exist to refresh the DECLARATIONS (the tools,
    # the skills, the prompt template — the sets #855 pins), and none of those
    # need a database. Refusing outright would make a declaration refresh
    # impossible without a production snapshot, for no gain. What must never
    # happen is a real capture being replaced by an empty one.
    try:
        preserved = plan_capture(data.get("assembled"), TARGET.read_text(encoding="utf-8"))
        data.update(preserved)
    except CaptureRefused as refusal:
        sys.exit(str(refusal))

    blob = json.dumps(data, default=str, separators=(",", ":"))
    line = f"const CHAT = {blob};\n"

    text = TARGET.read_text(encoding="utf-8")
    # lambda, not the string: the JSON blob carries \u escapes that re would
    # otherwise read as replacement-template escapes and reject.
    new, n = re.subn(r"(?m)^const CHAT = .*$\n", lambda _m: line, text, count=1)
    if not n:
        sys.exit(f"could not find the generated `const CHAT = ...` line in {TARGET}")
    TARGET.write_text(new, encoding="utf-8")

    cap = data["assembled"]
    for conv in (cap.get("conversations") or []):
        print(f"  · {conv['display_title'][:44]:<44} "
              f"{conv['turn_count']:>2} turns · {conv['tool_calls']:>2} tool calls · "
              f"{conv['skill_loads']} skills · from {conv['started_from'] or '—'} · "
              f"prompt {conv['prompt_chars']}")
    print(f"wrote {TARGET.relative_to(Path.cwd()) if TARGET.is_relative_to(Path.cwd()) else TARGET}")
    print(f"  prompt template   {len(data['template'])} chars, slots: {', '.join(data['slot_order'])}")
    print(f"  tools             {len(data['tools']['data'])} data + action + skill")
    # main() hard-codes each key it reports, so a new pinned set gets no console
    # line unless one is added here.
    print(f"  screen builders   {', '.join(data['screen_builders']) or 'none'}")
    forced = (data['meta'].get('forced') or {})
    print(f"  coach flags       {len(data['meta'].get('coach_flags') or {})} pinned to "
          f"{data['meta'].get('flag_contract') or 'the local .env'}"
          f"{' · forced: ' + ', '.join(sorted(forced)) if forced else ' · nothing to force'}")
    print(f"  skills            {len(data['skills'])} "
          f"({data['skill_cost']['catalogue_chars']} chars catalogue / "
          f"{data['skill_cost']['procedure_chars']} held back)")
    if cap.get("ok"):
        if not preserved:  # a kept capture's count would describe an earlier run
            print(f"  screens resolved  {_screen_resolution_summary(cap.get('conversations') or [])}")
        present = [k for k, v in cap["present"].items() if v]
        print(f"  assembled capture {cap['chars']} chars; slots present: {', '.join(present) or 'none'}")
    else:
        print(f"  assembled capture skipped — {cap.get('reason')}")


if __name__ == "__main__":
    main()
