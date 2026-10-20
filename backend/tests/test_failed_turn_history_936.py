"""A failed turn is shown to the runner, never sent back to the model (#936).

Production, 18 Aug: two turns failed in a row, each stored as an assistant row
carrying the failure text, and the retry sent the model its own error as a prior
coach reply. The cross-thread digest already refused that text; the thread's own
history did not. Both readers now share one definition of a failed turn.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach import threads as thread_service
from app.services.coach.chat import (
    MEDICAL_REDIRECT_MESSAGE,
    RATE_LIMITED_FAIL_MESSAGE,
    STREAM_FAIL_MESSAGE,
)
from app.services.coach.thread_turn import _build_cross_thread_block, _llm_history

T0 = datetime(2026, 8, 18, 10, 20, tzinfo=timezone.utc)


def _user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _thread(db, user) -> Thread:
    thread = Thread(user_id=user.id)
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


def _production_thread(db, user, failure=STREAM_FAIL_MESSAGE) -> Thread:
    """The 18 Aug thread as stored, then the runner's next message."""
    thread = _thread(db, user)
    _say(db, thread, "user", "Hello, I'm wondering which session i should do "
         "today the 5k easy or 5k quality session?", minutes=0)
    _say(db, thread, "assistant", failure, minutes=0)
    _say(db, thread, "user", "Try again", minutes=4)
    _say(db, thread, "assistant", failure, minutes=4)
    _say(db, thread, "user", "Try again", minutes=6)
    return thread


def test_a_retry_does_not_send_the_failure_as_a_coach_turn(db):
    thread = _production_thread(db, _user(db))

    history = _llm_history(db, thread)

    assert all(m["role"] == "user" for m in history)
    assert [m["content"] for m in history][1:] == ["Try again", "Try again"]


def test_a_rate_limited_failure_is_not_a_coach_turn_either(db):
    thread = _production_thread(db, _user(db), failure=RATE_LIMITED_FAIL_MESSAGE)

    assert all(m["role"] == "user" for m in _llm_history(db, thread))


def test_the_runner_still_sees_the_failure_in_the_transcript(db):
    thread = _production_thread(db, _user(db))

    shown = [m.content for m in thread_service.thread_messages(db, thread)]

    assert shown.count(STREAM_FAIL_MESSAGE) == 2


def test_a_real_reply_and_the_medical_redirect_stay_in_the_threads_history(db):
    """In its own thread the coach must see that it already declined, so the
    redirect is kept here even though the cross-thread digest drops it."""
    thread = _thread(db, _user(db))
    _say(db, thread, "user", "Easy or quality today?", minutes=0)
    _say(db, thread, "assistant", "Easy today, quality on Thursday.", minutes=1)
    _say(db, thread, "user", "My knee is swollen, should I run?", minutes=2)
    _say(db, thread, "assistant", MEDICAL_REDIRECT_MESSAGE, minutes=3)

    coach = [m["content"] for m in _llm_history(db, thread) if m["role"] == "assistant"]

    assert coach == ["Easy today, quality on Thursday.", MEDICAL_REDIRECT_MESSAGE]


def test_the_digest_agrees_a_rate_limited_failure_is_not_conversation(db):
    user = _user(db)
    current = _thread(db, user)
    _production_thread(db, user, failure=RATE_LIMITED_FAIL_MESSAGE)

    block = _build_cross_thread_block(db, current, user.id)

    assert "Try again" in block
    assert "a lot of requests" not in block
