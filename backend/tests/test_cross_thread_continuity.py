"""The cross-THREAD digest: a new thread is a topic boundary, not a memory one.

ADR 0027. A thread turn carries a bounded digest of the runner's recent turns
from their OTHER threads, so starting a fresh conversation does not reset the
coach. This replaced the read-time cross-ACTIVITY digest that simulated the same
continuity before threads were real (#770 retired it with the activity chat box);
the bounds it was held to carry over, because the failure they prevent — an
injected history that grows without limit as the relationship accumulates — is
the same one.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach.chat import MEDICAL_REDIRECT_MESSAGE
from app.services.coach.thread_turn import (
    _MAX_CROSS_THREAD_CHARS,
    _MAX_CROSS_THREAD_TURNS,
    _build_cross_thread_block,
)

T0 = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)


def _user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _thread(db, user, *, title=None) -> Thread:
    thread = Thread(user_id=user.id, title=title)
    db.add(thread)
    db.commit()
    return thread


def _say(db, thread, role, content, *, minutes=0):
    db.add(CoachChatMessage(
        thread_id=thread.id,
        role=role,
        content=content,
        created_at=T0 + timedelta(minutes=minutes),
    ))
    db.commit()


def test_the_current_thread_is_not_digested_into_itself(db):
    """This thread's own turns are its history, not its cross-thread context — no
    double-counting."""
    user = _user(db)
    current = _thread(db, user)
    _say(db, current, "assistant", "OWNTHREAD this conversation's own turn.")

    assert "OWNTHREAD" not in _build_cross_thread_block(db, current, user.id)


def test_another_runners_threads_are_never_digested(db):
    """The digest is user-scoped: one runner's conversations can never surface in
    another's."""
    user, stranger = _user(db), _user(db)
    current = _thread(db, user)
    theirs = _thread(db, stranger)
    _say(db, theirs, "user", "THEIRSECRET how do I fix my knee")

    assert "THEIRSECRET" not in _build_cross_thread_block(db, current, user.id)


def test_the_digest_is_bounded(db):
    user = _user(db)
    current = _thread(db, user)
    other = _thread(db, user)
    for i in range(_MAX_CROSS_THREAD_TURNS + 5):
        _say(db, other, "user", f"question number {i}", minutes=i)

    block = _build_cross_thread_block(db, current, user.id)
    entries = [ln for ln in block.splitlines() if ln.startswith(("Runner:", "You:"))]
    assert len(entries) == _MAX_CROSS_THREAD_TURNS


def test_a_long_turn_is_truncated(db):
    """One verbose turn cannot blow the token budget."""
    user = _user(db)
    current = _thread(db, user)
    other = _thread(db, user)
    long_msg = "x" * (_MAX_CROSS_THREAD_CHARS + 200)
    _say(db, other, "assistant", long_msg)

    block = _build_cross_thread_block(db, current, user.id)
    assert long_msg not in block
    assert "…" in block


def test_error_and_redirect_sentinels_are_not_conversation(db):
    """A safe-redirect or error sentinel is not something the coach said; threading
    it forward would have the coach echo its own failure back as continuity."""
    user = _user(db)
    current = _thread(db, user)
    other = _thread(db, user)
    _say(db, other, "assistant", MEDICAL_REDIRECT_MESSAGE, minutes=1)
    _say(db, other, "assistant", "Sorry, I encountered an error. Please try again.", minutes=2)
    _say(db, other, "assistant", "REALADVICE ease back to easy mileage this week.", minutes=3)

    block = _build_cross_thread_block(db, current, user.id)
    assert "REALADVICE" in block
    assert MEDICAL_REDIRECT_MESSAGE not in block
    assert "I encountered an error" not in block


def test_a_runners_first_thread_carries_no_digest(db):
    """Nothing to continue means an empty block, not an empty heading."""
    user = _user(db)
    only = _thread(db, user)
    _say(db, only, "user", "this is my only conversation")

    assert _build_cross_thread_block(db, only, user.id) == ""
