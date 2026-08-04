"""Thread resolution and upkeep (ADR 0027, #765).

A `Thread` is the runner-initiated conversation unit: relationship-scoped,
resumable, optionally anchored to an activity. This module owns resolving the
thread the activity chat box writes through, adopting any orphan rows written by
pre-thread code, and keeping `last_message_at` honest for the switcher ordering.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.models.activity import Activity
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread


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
