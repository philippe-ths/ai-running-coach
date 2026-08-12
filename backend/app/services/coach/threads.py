"""Thread resolution and upkeep (ADR 0027, #765).

A `Thread` is the runner-initiated conversation unit: relationship-scoped,
resumable, optionally anchored to an activity. This module owns resolving the
thread the activity chat box writes through, adopting any orphan rows written by
pre-thread code, and keeping `last_message_at` honest for the switcher ordering.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql import func

from app.models.activity import Activity
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.schemas.thread import ThreadAnchor, ThreadListItem

# Switcher bounds: enough for years of conversations, small enough to render.
_MAX_LISTED_THREADS = 100
_DISPLAY_TITLE_CHARS = 60
_SNIPPET_CHARS = 90
_UNTITLED = "New thread"


def _title_from_first_message(content: Optional[str]) -> Optional[str]:
    """The trimmed first user message, or None when there is nothing usable and
    the caller should fall back to the placeholder name."""
    if not content:
        return None
    text = " ".join(content.split())
    return text[:_DISPLAY_TITLE_CHARS].rstrip() + (
        "…" if len(text) > _DISPLAY_TITLE_CHARS else ""
    )


def _snippet_from_last_message(content: Optional[str]) -> Optional[str]:
    """The trimmed last message of the thread, or None when there is none."""
    if not content:
        return None
    text = " ".join(content.split())
    return text[:_SNIPPET_CHARS] + ("…" if len(text) > _SNIPPET_CHARS else "")


def _first_user_message_content(db: Session, thread_id) -> Optional[str]:
    row = (
        db.query(CoachChatMessage.content)
        .filter(
            CoachChatMessage.thread_id == thread_id,
            CoachChatMessage.role == "user",
        )
        .order_by(CoachChatMessage.created_at.asc(), CoachChatMessage.id.asc())
        .first()
    )
    return row[0] if row else None


def display_title(db: Session, thread: Thread) -> str:
    """The thread's name for the switcher: the written/runner title when set,
    else the first user message trimmed. An untitled thread needs a name the
    moment it has one turn in it (design spec)."""
    if thread.title:
        return thread.title
    title = _title_from_first_message(_first_user_message_content(db, thread.id))
    return _UNTITLED if title is None else title


def _anchor_for(thread: Thread) -> Optional[ThreadAnchor]:
    if thread.activity_id is None:
        return None
    activity = thread.activity
    if activity is None:
        return ThreadAnchor(activity_id=thread.activity_id)
    return ThreadAnchor(
        activity_id=activity.id,
        name=activity.name,
        start_date=activity.start_date,
        distance_m=activity.distance_m,
        type=activity.type,
    )


def _one_message_per_thread(
    db: Session,
    thread_ids: Sequence,
    *,
    newest: bool,
    user_turns_only: bool = False,
) -> Dict:
    """One message per thread in a single query: the newest, or the oldest user
    turn, under the same ordering the per-thread reads used (#788).

    `ROW_NUMBER() OVER (PARTITION BY …)` is plain SQL:2003, evaluated identically
    by Postgres (production) and SQLite >= 3.25 (the test suite's create_all
    database), and the ORDER BY inside the window is byte-for-byte the one the
    replaced per-thread queries carried, so tie-breaking cannot drift between the
    two implementations on either dialect.
    """
    if not thread_ids:
        return {}
    order = (
        (CoachChatMessage.created_at.desc(), CoachChatMessage.id.desc())
        if newest
        else (CoachChatMessage.created_at.asc(), CoachChatMessage.id.asc())
    )
    conditions = [CoachChatMessage.thread_id.in_(thread_ids)]
    if user_turns_only:
        conditions.append(CoachChatMessage.role == "user")
    ranked = (
        select(
            CoachChatMessage.thread_id.label("thread_id"),
            CoachChatMessage.content.label("content"),
            func.row_number()
            .over(partition_by=CoachChatMessage.thread_id, order_by=order)
            .label("rank"),
        )
        .where(*conditions)
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.thread_id, ranked.c.content).where(ranked.c.rank == 1)
    ).all()
    return {thread_id: content for thread_id, content in rows}


def list_threads(db: Session, user_id) -> List[ThreadListItem]:
    """The runner's threads for the switcher, most recent conversation first.

    Three queries for the whole list rather than three per thread (#788): the
    threads with their anchors joined, the last message of each, and the first
    user message of the untitled ones. The output is unchanged.
    """
    threads = (
        db.query(Thread)
        .options(joinedload(Thread.activity))
        .filter(Thread.user_id == user_id)
        .order_by(
            func.coalesce(Thread.last_message_at, Thread.created_at).desc(),
            Thread.id.desc(),
        )
        .limit(_MAX_LISTED_THREADS)
        .all()
    )
    thread_ids = [thread.id for thread in threads]
    last_messages = _one_message_per_thread(db, thread_ids, newest=True)
    untitled_ids = [thread.id for thread in threads if not thread.title]
    first_user_messages = _one_message_per_thread(
        db, untitled_ids, newest=False, user_turns_only=True
    )

    items: List[ThreadListItem] = []
    for thread in threads:
        if thread.title:
            title = thread.title
        else:
            trimmed = _title_from_first_message(first_user_messages.get(thread.id))
            title = _UNTITLED if trimmed is None else trimmed
        items.append(
            ThreadListItem(
                id=thread.id,
                title=title,
                snippet=_snippet_from_last_message(last_messages.get(thread.id)),
                last_message_at=thread.last_message_at,
                anchor=_anchor_for(thread),
            )
        )
    return items


def thread_messages(db: Session, thread: Thread) -> List[CoachChatMessage]:
    """Full scrollback, chronological (the LLM input is bounded separately —
    the runner never loses history to a cost bound)."""
    return (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread.id)
        .order_by(CoachChatMessage.created_at.asc(), CoachChatMessage.id.asc())
        .all()
    )


def get_owned_thread(db: Session, thread_id, user_id) -> Optional[Thread]:
    """Owner-scoped thread fetch: a cross-user id resolves to nothing."""
    return (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.user_id == user_id)
        .first()
    )


def delete_thread(db: Session, thread: Thread) -> None:
    """Hard delete: the thread and its messages. Deletion works structurally
    (ADR 0027): the memory writer rebuilds from source, so the next pass simply
    does not see these messages."""
    db.query(CoachChatMessage).filter(
        CoachChatMessage.thread_id == thread.id
    ).delete(synchronize_session=False)
    db.delete(thread)
    db.commit()


def resolve_thread_for_activity(
    db: Session, activity: Activity, *, create: bool = False
) -> Optional[Thread]:
    """The thread anchored to this activity, oldest first (the migration made
    exactly one per activity-with-chat; oldest-first keeps resolution
    deterministic if a later slice ever allows several).

    With ``create=True`` a missing thread is created owned by the activity's
    owner — the write-through path for the activity chat box. With
    ``create=False`` an EMPTY thread is never created, but one is still created
    when orphan messages exist for the activity (rows written by pre-thread code
    during a deploy window), so legacy history is never invisible to a
    thread-scoped read. Orphans are adopted into the resolved thread either way:
    the data converges to every-message-has-a-thread without a schema-level
    constraint (#515 reconcile idiom).
    """
    thread = (
        db.query(Thread)
        .filter(Thread.activity_id == activity.id)
        .order_by(Thread.created_at.asc(), Thread.id.asc())
        .first()
    )
    if thread is None:
        if not create and not _has_orphan_messages(db, activity):
            return None
        thread = Thread(user_id=activity.user_id, activity_id=activity.id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
    _adopt_orphan_messages(db, thread)
    return thread


def _has_orphan_messages(db: Session, activity: Activity) -> bool:
    return (
        db.query(CoachChatMessage.id)
        .filter(
            CoachChatMessage.activity_id == activity.id,
            CoachChatMessage.thread_id.is_(None),
        )
        .first()
        is not None
    )


def _adopt_orphan_messages(db: Session, thread: Thread) -> None:
    """Attach thread-less rows carrying this thread's activity anchor (#515
    reconcile idiom: idempotent, converges, safe to run on every resolve)."""
    if thread.activity_id is None:
        return
    adopted = (
        db.query(CoachChatMessage)
        .filter(
            CoachChatMessage.activity_id == thread.activity_id,
            CoachChatMessage.thread_id.is_(None),
        )
        .update({CoachChatMessage.thread_id: thread.id}, synchronize_session=False)
    )
    if adopted:
        db.commit()


def delete_threads_for_activity(db: Session, activity: Activity) -> None:
    """Clear the activity's conversation: its anchored threads, their messages,
    and any orphan pre-thread rows still carrying the activity id. The caller's
    endpoint behaviour is unchanged from the pre-thread delete (#765)."""
    thread_ids = [
        tid
        for (tid,) in db.query(Thread.id).filter(Thread.activity_id == activity.id)
    ]
    query = db.query(CoachChatMessage)
    if thread_ids:
        query = query.filter(
            (CoachChatMessage.activity_id == activity.id)
            | (CoachChatMessage.thread_id.in_(thread_ids))
        )
    else:
        query = query.filter(CoachChatMessage.activity_id == activity.id)
    query.delete(synchronize_session=False)
    if thread_ids:
        db.query(Thread).filter(Thread.id.in_(thread_ids)).delete(
            synchronize_session=False
        )
    db.commit()


def touch_thread(db: Session, thread: Thread) -> None:
    """Record that a message just landed on this thread (switcher recency)."""
    thread.last_message_at = func.now()
    db.add(thread)
