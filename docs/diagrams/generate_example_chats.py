"""Generate example coach-chat conversations for the flow diagram.

The diagram shows REAL captured turns, so its coverage is only as good as the
conversations that exist. A seeded local database carries whatever the runner
happened to ask, which left several paths through the turn unexercised: no
conversation had ever loaded a coaching skill, and none had started from Home
or Load.

This script fills those gaps by running the REAL turn service (no fixtures, no
stubs) for a set of scenarios chosen to reach each path, then leaves the rows
for generate_chat_flow_data.py to capture.

It runs against the LOCAL database only (it refuses anything else) and makes
real Anthropic calls, so it costs money and should be run deliberately:

    cd backend && python ../docs/diagrams/generate_example_chats.py

Add --dry-run to print the scenarios and the resolved config without calling
the model. Add --only <name> to run one scenario.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from sqlalchemy import func  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Activity, CoachChatMessage, Thread, User  # noqa: E402
from app.schemas.thread import ScreenPointer  # noqa: E402
from app.services.coach import thread_turn  # noqa: E402

# The prompt production runs. The local .env is usually set to something else
# (a rollback target, a prompt under test), and a conversation captured under a
# different prompt would show a system prompt prod never sent.
PROD_PROMPT_ID = os.environ.get("PROMPT_ID", "coach_message_lean_grouped_v7")


@dataclass
class Scenario:
    name: str
    # Why this scenario exists: the path through the turn it is meant to reach.
    covers: str
    screen: str
    messages: list[str]
    range: Optional[str] = None
    types: Optional[list[str]] = None
    # activity scenarios pick their own subject at run time
    needs_activity: bool = False
    activity_pick: str = "recent_run"
    notes: list[str] = field(default_factory=list)


SCENARIOS = [
    Scenario(
        name="home_plan_the_week",
        covers="entry from Home · plan_the_week skill · data tools",
        screen="home",
        messages=[
            "What should my running week look like from here?",
            "Can I fit the long run on Saturday instead?",
        ],
    ),
    Scenario(
        name="load_readiness",
        covers="entry from Load · readiness reading · data tools",
        screen="load",
        messages=[
            "Am I digging myself into a hole here, or is this sustainable?",
            "What would you change about next week?",
        ],
    ),
    Scenario(
        name="trends_compare_sessions",
        covers="entry from Trends · compare_sessions skill · get_session_detail",
        screen="trends",
        range="3M",
        messages=[
            "How did my last two long runs compare to each other?",
            "Which one was the better session, really?",
        ],
    ),
    Scenario(
        name="activity_explain_a_metric",
        covers="entry from an activity · explain_a_metric skill · anchored thread",
        screen="activity",
        needs_activity=True,
        messages=[
            "What is HR drift, and is mine on this run any good?",
            "So should I be doing anything differently about it?",
        ],
    ),
]


def _pin_config() -> dict:
    """Match production, and report anything forced.

    The generator that captures these conversations pins the same way, so a
    conversation is captured under the prompt it was generated under.
    """
    forced = {}
    if settings.COACH_PROMPT_ID != PROD_PROMPT_ID:
        forced["COACH_PROMPT_ID"] = f"{settings.COACH_PROMPT_ID} -> {PROD_PROMPT_ID}"
        settings.COACH_PROMPT_ID = PROD_PROMPT_ID
    for flag in (
        "COACH_VOICE_BLOCK_ENABLED",
        "COACH_MEMORY_ENABLED",
        "COACH_THREADS_ENABLED",
        "COACH_RELATIONSHIP_ENABLED",
    ):
        if hasattr(settings, flag) and not getattr(settings, flag):
            forced[flag] = "False -> True"
            setattr(settings, flag, True)
    return forced


def _assert_local(db) -> str:
    url = str(settings.DATABASE_URL)
    if not ("localhost" in url or "127.0.0.1" in url):
        raise SystemExit(f"refusing to write to a non-local database: {url}")
    return url


def _pick_user(db) -> User:
    """The runner with the most chat history: the deployment owner on a seed,
    and the only one whose baseline is rich enough to be worth showing."""
    row = (
        db.query(Thread.user_id, func.count(CoachChatMessage.id).label("n"))
        .join(CoachChatMessage, CoachChatMessage.thread_id == Thread.id)
        .group_by(Thread.user_id)
        .order_by(func.count(CoachChatMessage.id).desc())
        .first()
    )
    if row is None:
        row = (db.query(Activity.user_id, func.count(Activity.id))
               .group_by(Activity.user_id)
               .order_by(func.count(Activity.id).desc())
               .first())
    if row is None:
        raise SystemExit("no user with any data in the local database — run `make seed-local`")
    return db.query(User).filter(User.id == row[0]).one()


def _pick_activity(db, user_id) -> Activity:
    """A recent analysed run — the kind of session an activity-page question
    is actually asked about."""
    act = (
        db.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.is_deleted.is_(False),
            Activity.type == "Run",
        )
        .order_by(Activity.start_date.desc())
        .first()
    )
    if act is None:
        raise SystemExit("no run found for this user in the local database")
    return act


def _pointer(sc: Scenario, activity: Optional[Activity]) -> ScreenPointer:
    kwargs: dict = {"screen": sc.screen}
    if sc.screen == "activity" and activity is not None:
        kwargs["activity_id"] = activity.id
    if sc.range:
        kwargs["range"] = sc.range
    if sc.types:
        kwargs["types"] = sc.types
    return ScreenPointer(**kwargs)


async def _run_turn(db, user, message, thread, anchor, asked_from, screen) -> dict:
    """Drive the real turn and collect what the client would have seen."""
    text, tools, skills, status, proposed = [], [], [], [], None
    async for ev in thread_turn.stream_thread_turn(
        db,
        user,
        message=message,
        thread=thread,
        anchor_activity=anchor,
        asked_from=asked_from,
        screen=screen,
    ):
        if ev.is_heartbeat:
            continue
        if ev.thread_meta is not None:
            thread_id = ev.thread_meta.get("thread_id")
            if thread is None and thread_id:
                thread = db.query(Thread).filter(Thread.id == thread_id).one()
            continue
        if ev.proposed_action is not None:
            proposed = ev.proposed_action
            continue
        if ev.status_label:
            status.append(ev.status_label)
            continue
        if ev.trace_entry is not None:
            tools.append(ev.trace_entry.get("tool"))
            continue
        if ev.text:
            text.append(ev.text)
    if thread is not None:
        db.refresh(thread)
        last = (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.thread_id == thread.id,
                    CoachChatMessage.role == "assistant")
            .order_by(CoachChatMessage.created_at.desc())
            .first()
        )
        if last is not None:
            skills = list(last.skills_used or [])
    return {
        "thread": thread,
        "chars": len("".join(text)),
        "tools": [t for t in tools if t],
        "skills": skills,
        "status": status,
        "proposed": proposed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    forced = _pin_config()
    scenarios = [s for s in SCENARIOS if not args.only or s.name == args.only]
    if not scenarios:
        raise SystemExit(f"no scenario named {args.only!r}")

    db = SessionLocal()
    try:
        url = _assert_local(db)
        user = _pick_user(db)
        print(f"database : {url}")
        print(f"runner   : {user.id}")
        print(f"prompt   : {settings.COACH_PROMPT_ID}")
        print(f"model    : {settings.COACH_CHAT_MODEL_ID or settings.COACH_MODEL_ID}")
        if forced:
            print(f"forced   : {forced}")
        print()

        turns = sum(len(s.messages) for s in scenarios)
        print(f"{len(scenarios)} scenarios, {turns} turns:")
        for s in scenarios:
            print(f"  - {s.name:26s} {s.covers}")
        print()
        if args.dry_run:
            print("dry run — no model calls made.")
            return 0

        for sc in scenarios:
            activity = _pick_activity(db, user.id) if sc.needs_activity else None
            pointer = _pointer(sc, activity)
            print(f"--- {sc.name} (screen={sc.screen}"
                  + (f", activity={activity.id}" if activity else "") + ")")
            thread = None
            for i, msg in enumerate(sc.messages, 1):
                res = asyncio.run(_run_turn(
                    db, user, msg,
                    thread,
                    activity if (thread is None and activity is not None) else None,
                    sc.screen, pointer,
                ))
                thread = res["thread"]
                print(f"  Q{i}: {msg}")
                print(f"      reply {res['chars']} chars"
                      f" · tools {res['tools'] or '-'}"
                      f" · skills {res['skills'] or '-'}"
                      + (" · proposed_action" if res["proposed"] else ""))
            if thread is not None:
                print(f"      thread {thread.id}")
            print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
