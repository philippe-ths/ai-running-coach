"""#1003: a confirmed amendment can be watched, and says when it arrives.

#998 moved the amendment onto the worker, which is what made it work, and
inherited the gap #879 had already closed for drafts. `useDraftStatus` named it:

    Only the empty-week state ever watched for the answer, so a runner who
    already had a plan was told nothing at all: not that it had started, not
    that it had landed, not that it had failed.

An amendment was in that position with one thing worse. A draft has a
`training_plans` row carrying `drafting`/`active`/`failed`, so there is something
to poll; an amendment deliberately leaves no row while it runs, because it either
replaces the window or leaves the plan exactly as it was. So the state it now
publishes is the only thing that makes it observable at all.

Reported after the first real use: "the coach doesn't come back and tell me it's
been added. I had to refresh the page."

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import uuid
from datetime import date
from unittest.mock import patch

import pytest

from app.jobs import amend_schedule
from app.models import User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.schedule import amend_watch
from app.services.schedule.amend import AmendOutcome


class _FakeRedis:
    """Enough of the interface `amend_watch` uses, with a real expiry argument."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(amend_watch, "redis_conn", fake)
    return fake


def _user(db) -> User:
    user = User(email=f"watch1003-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --- the state itself --------------------------------------------------------


def test_nothing_to_say_is_none_rather_than_a_shape(redis):
    assert amend_watch.current(uuid.uuid4()) is None


def test_a_started_amendment_reports_its_window(redis):
    uid = uuid.uuid4()
    amend_watch.mark_started(uid, date(2026, 8, 31), date(2026, 9, 6))

    state = amend_watch.current(uid)
    assert state["status"] == "working"
    assert state["start"] == "2026-08-31"
    assert state["end"] == "2026-09-06"


def test_the_outcome_replaces_the_working_state(redis):
    uid = uuid.uuid4()
    amend_watch.mark_started(uid, date(2026, 8, 31), date(2026, 9, 6))
    amend_watch.mark_done(uid, date(2026, 8, 31), date(2026, 9, 6), ["Added: Mon Easy"])

    state = amend_watch.current(uid)
    assert state["status"] == "done"
    assert state["changes"] == ["Added: Mon Easy"]


def test_a_failure_carries_something_the_runner_can_read(redis):
    uid = uuid.uuid4()
    amend_watch.mark_failed(uid, date(2026, 8, 31), date(2026, 9, 6), "it does not fit")

    state = amend_watch.current(uid)
    assert state["status"] == "failed"
    assert state["detail"] == "it does not fit"


def test_the_state_expires_rather_than_waiting_for_ever(redis):
    """A finished amendment must not still be announcing itself tomorrow."""
    uid = uuid.uuid4()
    amend_watch.mark_started(uid, date(2026, 8, 31), date(2026, 9, 6))
    (ttl,) = set(redis.ttls.values())
    assert 0 < ttl <= 3600


def test_one_runners_amendment_is_not_anothers(redis):
    a, b = uuid.uuid4(), uuid.uuid4()
    amend_watch.mark_started(a, date(2026, 8, 31), date(2026, 9, 6))
    assert amend_watch.current(b) is None


def test_a_shown_outcome_can_be_cleared(redis):
    uid = uuid.uuid4()
    amend_watch.mark_done(uid, date(2026, 8, 31), date(2026, 9, 6))
    amend_watch.clear(uid)
    assert amend_watch.current(uid) is None


def test_a_broken_redis_never_breaks_the_amendment(monkeypatch):
    """The watching is the consolation prize; the plan is the work.

    Every writer here is called from the middle of doing something real - the
    confirm request, or the job that just wrote the week - so a Redis that is
    down must cost the runner a spinner, not their sessions.
    """
    class _Broken:
        def set(self, *a, **k):
            raise RuntimeError("redis is down")

        def get(self, *a, **k):
            raise RuntimeError("redis is down")

        def delete(self, *a, **k):
            raise RuntimeError("redis is down")

    monkeypatch.setattr(amend_watch, "redis_conn", _Broken())
    uid = uuid.uuid4()
    amend_watch.mark_started(uid, date(2026, 8, 31), date(2026, 9, 6))
    amend_watch.mark_done(uid, date(2026, 8, 31), date(2026, 9, 6))
    amend_watch.mark_failed(uid, date(2026, 8, 31), date(2026, 9, 6), "x")
    amend_watch.clear(uid)
    assert amend_watch.current(uid) is None


# --- the job publishes it, and speaks -----------------------------------------


class _NoCloseSession:
    """The job opens its own session; hand it the test's and keep it open."""

    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    def __getattr__(self, name):
        return getattr(self._db, name)

    def close(self):
        pass


def _run_job(db, user, plan, outcome, thread):
    with patch.object(
        amend_schedule, "SessionLocal", return_value=_NoCloseSession(db)
    ), patch.object(amend_schedule, "amend_plan", return_value=outcome):
        amend_schedule.amend_schedule_job(
            str(user.id), str(plan.id), 1, 1, "reason",
            str(thread.id), "Rewrite the week.",
        )


def _plan(db, user):
    from app.models.training_plan import TrainingPlan

    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _thread(db, user):
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def _said(db, thread):
    return [
        m
        for m in db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread.id)
        .all()
    ]


def test_a_landed_amendment_is_published_and_said(db, redis):
    """Both halves of "it arrived": the screen can see it, and the coach says it."""
    user = _user(db)
    plan = _plan(db, user)
    thread = _thread(db, user)

    _run_job(
        db, user, plan,
        AmendOutcome(ok=True, weeks_touched=1, sessions_written=7, changes=["Added: Mon Easy"]),
        thread,
    )

    state = amend_watch.current(user.id)
    assert state["status"] == "done"

    said = _said(db, thread)
    note = [m for m in said if m.role == "assistant"]
    assert len(note) == 1, "the coach has to come back and say the week is in"
    assert "7 sessions" in note[0].content
    # And the ledger entry beside it, which is for the COACH rather than the
    # runner and is filtered out of what they read.
    assert any(m.role == "event" for m in said)


def test_a_failed_amendment_is_published_and_said(db, redis):
    user = _user(db)
    plan = _plan(db, user)
    thread = _thread(db, user)

    _run_job(
        db, user, plan,
        AmendOutcome(ok=False, failures=["it does not fit"]),
        thread,
    )

    assert amend_watch.current(user.id)["status"] == "failed"
    note = [m for m in _said(db, thread) if m.role == "assistant"]
    assert len(note) == 1
    assert "could not write" in note[0].content
    assert not any(m.role == "event" for m in _said(db, thread)), (
        "nothing was written, so the ledger must not say it was"
    )


# --- the guards bite ----------------------------------------------------------


def test_the_landing_guard_would_fail_without_the_note(db, redis, monkeypatch):
    """Prove the assertion above bites, rather than passing on an empty thread."""
    monkeypatch.setattr(amend_schedule, "_say_it_landed", lambda *a, **k: None)
    user = _user(db)
    plan = _plan(db, user)
    thread = _thread(db, user)

    _run_job(db, user, plan, AmendOutcome(ok=True, weeks_touched=1, sessions_written=7), thread)

    assert [m for m in _said(db, thread) if m.role == "assistant"] == []
