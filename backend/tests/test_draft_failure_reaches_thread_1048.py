"""#1048: a plan confirmed in chat that then fails says so in that chat.

Observed 7 Oct: a draft confirmed from the coach card failed on the worker
(`week 2026-10-05 cannot satisfy its own rule 'At most two sessions in a day'`).
The status read `failed`, but the conversation's last word stayed the card the
runner confirmed, and nothing in the thread said the plan never arrived. The
amendment job already writes a coach note when it fails (#984); the draft job
now does the same, with the sentence the draft status serves.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.jobs import generate_schedule as job_mod
from app.models import User
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.schedule import store
from app.services.schedule.draft import DraftOutcome
from app.services.schedule.draft_messages import failure_message

OBSERVED_FAILURE = "week 2026-10-05 cannot satisfy its own rule 'At most two sessions in a day'"


@pytest.fixture(autouse=True)
def _season_in_place():
    """The season step is not under test; the weeks are."""
    async def _none(*_args, **_kwargs):
        return None

    with patch.object(job_mod, "_ensure_season", _none):
        yield


def _runner_and_thread(db):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()
    return user, thread


def _run(db, user, plan, thread, draft):
    """Run the job, then read back the coach's notes in the thread. Ids are
    taken first: the job closes the session it is handed."""
    user_id, plan_id, thread_id = str(user.id), str(plan.id), thread.id
    with patch.object(job_mod, "SessionLocal", return_value=db), patch.object(
        job_mod, "draft_plan", draft
    ):
        job_mod.generate_schedule_job(
            user_id, plan_id, str(thread_id), "Put it in my schedule"
        )
    return [
        m.content
        for m in db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread_id, CoachChatMessage.role == "assistant")
        .all()
    ]


def test_the_observed_failure_is_said_in_the_thread_it_was_confirmed_in(db):
    user, thread = _runner_and_thread(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _rejected(*_args, **_kwargs):
        return DraftOutcome(ok=False, failures=[OBSERVED_FAILURE])

    notes = _run(db, user, plan, thread, _rejected)
    assert len(notes) == 1
    assert "did not make it into your schedule" in notes[0]
    assert failure_message(store.FAILURE_UNKNOWN) in notes[0]
    # The gate's machinery never reaches the runner.
    assert "2026-10-05" not in notes[0]
    assert "At most two sessions" not in notes[0]


def test_the_note_carries_the_same_sentence_as_the_draft_status(db):
    user, thread = _runner_and_thread(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _too_big(*_args, **_kwargs):
        return DraftOutcome(
            ok=False, failures=["ramp"], failure_kind=store.FAILURE_TOO_BIG_A_JUMP
        )

    notes = _run(db, user, plan, thread, _too_big)

    assert failure_message(store.FAILURE_TOO_BIG_A_JUMP) in notes[0]


def test_a_draft_that_throws_is_said_too(db):
    user, thread = _runner_and_thread(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("the generation exploded")

    notes = _run(db, user, plan, thread, _boom)
    assert len(notes) == 1
    assert "exploded" not in notes[0]


def test_a_plan_that_lands_leaves_no_failure_note(db):
    user, thread = _runner_and_thread(db)
    plan = store.create_drafting_plan(db, user.id)

    async def _ok(session, _user, target_plan, **_kwargs):
        target_plan.status = store.ACTIVE
        session.commit()
        return DraftOutcome(ok=True, plan_id=target_plan.id)

    assert _run(db, user, plan, thread, _ok) == []


def test_a_thread_deleted_while_the_draft_ran_still_lets_the_plan_fail(db):
    """#1123: the runner deleted the conversation mid-draft. On Postgres the note's
    insert violated the thread foreign key and the swallowed error left the
    session unable to mark the plan failed, so it read "being written"."""
    user, thread = _runner_and_thread(db)
    plan = store.create_drafting_plan(db, user.id)
    plan_id = plan.id
    db.delete(thread)
    db.commit()

    async def _rejected(*_args, **_kwargs):
        return DraftOutcome(ok=False, failures=[OBSERVED_FAILURE])

    notes = _run(db, user, plan, thread, _rejected)

    assert notes == []
    assert db.get(store.TrainingPlan, plan_id).status == store.FAILED
