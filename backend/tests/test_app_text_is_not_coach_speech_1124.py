"""#1124: text the app writes into a thread never reaches a model as speech.

Production snapshot, 21 Oct: the drafting model was handed "You: Sorry, I
encountered an error. Please try again." twice, another thread's digest carried
"You: Put it in my schedule" (an action event), and every thread with a failed
turn had no title, because the failure was counted as the first reply. Each
reader filtered differently, and a failure was recognised by its exact text, so
a new failure sentence joined the conversation by default. A failed turn or a
job's note is now written as a `notice`, outside the roles any reader takes, and
`threads.said` is the one filter, which still drops failures stored before it.
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.jobs import thread_maintenance
from app.models import Activity, User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach import chat
from app.services.coach import threads as thread_service
from app.services.coach.memory_update import gather_memory_sources
from app.services.coach.thread_turn import _build_cross_thread_block, _llm_history
from app.services.schedule.draft_facts import _conversation_block
from tests.test_chat_policy_validation import _post_chat_raising, _seed

EVENT = "Put it in my schedule"
NOTE = "Your coach could not be reached, so that plan did not make it into your schedule."


def _runner(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _thread(db, user, activity_id=None) -> Thread:
    thread = Thread(user_id=user.id, activity_id=activity_id)
    db.add(thread)
    db.commit()
    return thread


def _rows(db, thread, *pairs, activity_id=None):
    for role, content in pairs:
        db.add(CoachChatMessage(
            thread_id=thread.id, activity_id=activity_id, role=role, content=content,
        ))
        db.commit()


def _app_text_thread(db, user, activity_id=None) -> Thread:
    """What was said, interleaved with everything the app wrote: a failure
    stored before the notice role, an action event, and a job's note."""
    thread = _thread(db, user, activity_id)
    _rows(
        db, thread,
        ("user", "Build me a plan for the half"),
        ("assistant", chat.STREAM_FAIL_MESSAGE),
        ("user", "Try again"),
        ("assistant", "Here is a twelve-week shape."),
        ("event", EVENT),
        ("notice", NOTE),
        activity_id=activity_id,
    )
    return thread


def _leaks(text: str) -> list:
    return [t for t in (chat.STREAM_FAIL_MESSAGE, EVENT, NOTE) if t in text]


def test_another_threads_digest_carries_only_what_was_said(db):
    user = _runner(db)
    _app_text_thread(db, user)

    block = _build_cross_thread_block(db, _thread(db, user), user.id)

    assert "Here is a twelve-week shape." in block
    assert _leaks(block) == []


def test_the_drafting_model_reads_only_what_was_said(db):
    user = _runner(db)
    thread = _app_text_thread(db, user)

    block = _conversation_block(db, user, str(thread.id))

    assert "You: Here is a twelve-week shape." in block
    assert _leaks(block) == []


def test_the_threads_own_history_reads_only_what_was_said(db):
    user = _runner(db)
    thread = _app_text_thread(db, user)

    history = _llm_history(db, thread)

    assert [m["content"] for m in history if m["role"] == "assistant"] == [
        "Here is a twelve-week shape."
    ]
    assert _leaks(" ".join(m["content"] for m in history)) == []


def test_the_memory_writer_reads_only_what_was_said(db):
    user = _runner(db)
    activity = Activity(
        user_id=user.id, strava_activity_id=7, type="Run", name="Run",
        start_date=datetime(2026, 10, 20, 7, 0),
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, raw_summary={},
    )
    db.add(activity)
    db.commit()
    _app_text_thread(db, user, activity_id=activity.id)

    texts = " ".join(s.text for s in gather_memory_sources(db, user.id).sources)

    assert "twelve-week shape" in texts
    assert _leaks(texts) == []


def test_a_thread_whose_first_reply_failed_still_gets_its_title(db):
    """The title is enqueued on the first reply. A failure counted as that reply
    spent the one enqueue, and the thread stayed untitled."""
    user = _runner(db)
    thread = _thread(db, user)
    _rows(
        db, thread,
        ("user", "Build me a plan"),
        ("assistant", chat.STREAM_FAIL_MESSAGE),
        ("user", "Try again"),
        ("assistant", "Here is a twelve-week shape."),
    )

    with patch("app.core.queue.queue") as queue:
        thread_maintenance.enqueue_title_generation(db, thread)

    queue.enqueue.assert_called_once()


def test_a_failure_sentence_nobody_listed_still_stays_out(client, db, monkeypatch):
    """The guarantee rests on the role written, not on a list of texts: a new
    failure sentence is a notice the moment it is stored."""
    monkeypatch.setattr(chat, "STREAM_FAIL_MESSAGE", "A failure sentence added later.")
    activity = _seed(db)

    resp = _post_chat_raising(client, activity)

    assert resp.status_code == 200
    row = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.content == "A failure sentence added later.")
        .one()
    )
    assert row.role == thread_service.NOTICE_ROLE
    thread = db.get(Thread, row.thread_id)
    assert all(m["role"] == "user" for m in _llm_history(db, thread))
