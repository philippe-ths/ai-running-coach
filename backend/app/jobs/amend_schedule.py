"""The RQ job that amends part of a runner's training plan (#981).

Runner-triggered like the draft: a coach OFFERS an amendment, the runner confirms
it, and the confirm enqueues this. No background pass ever rewrites someone's week
without them agreeing to it, which is the property the whole offer-and-confirm
mechanism exists to hold.

Unlike the draft there is no row to fail. An amendment either replaces the window
or leaves the plan exactly as it was, so a failure here is silence plus a log
line, and the runner still has the plan they had a minute ago. That is the right
degradation for this operation: the draft creates a `drafting` row because there
is nothing to fall back to, whereas here the fallback is the plan itself.

The module path is a deploy contract. RQ serializes a job as its
`module.function` string, so a job sitting in Redis across a deploy binds to this
path by name.
"""

import asyncio
import logging
import uuid
from datetime import date

from app.db.session import SessionLocal
from app.models.user import User
from app.services.schedule import store
from app.services.schedule.amend import amend_plan

logger = logging.getLogger(__name__)


def amend_schedule_job(
    user_id: str,
    plan_id: str,
    weeks_from: int,
    weeks_through: int,
    instruction: str,
    thread_id: str | None = None,
    description: str | None = None,
) -> None:
    """Amend one window of one plan. Never raises out of the worker.

    `thread_id` and `description` are the conversation this was confirmed in and
    the card's own wording, carried so the ledger entry can be written HERE, once
    the sessions actually exist (#778's contract). Optional and trailing, so a
    job already sitting in Redis when this deploys still binds and runs.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uuid.UUID(str(user_id))).first()
        plan = (
            db.query(store.TrainingPlan)
            .filter(store.TrainingPlan.id == uuid.UUID(str(plan_id)))
            .first()
        )
        if user is None or plan is None:
            logger.warning(
                "schedule amend: user %s or plan %s is gone; nothing to do",
                user_id,
                plan_id,
            )
            return
        if plan.user_id != user.id:
            # Cannot happen through the confirm endpoint, which re-resolves the
            # plan against the authenticated runner. Checked anyway: this is the
            # one place a plan id and a user id arrive as separate arguments.
            logger.error(
                "schedule amend: plan %s does not belong to %s", plan_id, user_id
            )
            return
        if plan.status != store.ACTIVE:
            # The plan stopped being current between the card going up and the
            # runner tapping it. Amending a superseded plan would change a week
            # nobody is training to, silently.
            logger.info(
                "schedule amend: plan %s is %s, not active; nothing amended",
                plan_id,
                plan.status,
            )
            return

        from app.services.schedule import amend_watch
        from app.services.schedule.amend import resolve_window
        from app.services.weeks import resolve_week_start

        start, end = resolve_window(
            date.today(),
            resolve_week_start(getattr(user, "profile", None)),
            weeks_from=int(weeks_from),
            weeks_through=int(weeks_through),
        )
        outcome = asyncio.run(
            amend_plan(
                db,
                user,
                plan,
                weeks_from=int(weeks_from),
                weeks_through=int(weeks_through),
                instruction=instruction or "",
            )
        )
        if outcome.ok:
            logger.info(
                "schedule amend: plan %s amended over %s week(s), %s sessions written",
                plan.id,
                outcome.weeks_touched,
                outcome.sessions_written,
            )
            _record_in_thread(db, thread_id, description, outcome.changes)
            _say_it_landed(db, thread_id, outcome)
            amend_watch.mark_done(user.id, start, end, outcome.changes)
        else:
            logger.warning(
                "schedule amend: plan %s unchanged (%s): %s",
                plan.id,
                outcome.failure_kind,
                "; ".join(outcome.failures or ["unknown"]),
            )
            _say_it_failed(db, thread_id, outcome.failures)
            amend_watch.mark_failed(
                user.id, start, end, "; ".join(outcome.failures or [])
            )
    except Exception:
        logger.exception("schedule amend: job failed for plan %s", plan_id)
    finally:
        db.close()


def _say_it_landed(db, thread_id, outcome) -> None:
    """Tell the runner their week is in, in the thread they asked in (#1003).

    Separate from the ledger entry beside it, because they have different
    readers. `record_action_event` writes an `ACTION_EVENT_ROLE` row, which
    `threads.CONVERSATIONAL_ROLES` filters out of everything that reads what was
    SAID: it is what the COACH reads back as "already in their record", and the
    runner never sees it. So the amendment landed and the conversation went
    quiet, and the runner found out by reloading the page on a hunch.

    The wording is the coach's, not the ledger's, for the same reason: the ledger
    records the change, this tells someone about it.
    """
    weeks = getattr(outcome, "weeks_touched", 0) or 0
    written = getattr(outcome, "sessions_written", 0) or 0
    note = (
        f"That's in — {written} session{'s' if written != 1 else ''} written across "
        f"{weeks} week{'s' if weeks != 1 else ''}, on your Schedule screen now. "
        "Everything else in your plan is as it was."
    )
    try:
        from app.services.coach import threads as thread_service

        thread_service.record_coach_note(db, thread_id, note)
    except Exception:  # noqa: BLE001 - the week is written; the telling is not worth a raise
        logger.exception("schedule amend: landing note not written to %s", thread_id)


def _say_it_failed(db, thread_id, failures) -> None:
    """Tell the runner their week was not written, in the thread they asked in.

    The docstring above used to promise that a failure here was "silence plus a
    log line", on the reasoning that the runner still has the plan they had a
    minute ago. That reasoning holds for the PLAN and not for the runner: they
    tapped a card, were told it was being worked out, and then watched a Schedule
    screen that never changed, with nothing anywhere saying why (#984). Silence
    is the right degradation for the data and the wrong one for the person.

    Fail-soft, like its sibling: a note that cannot be written is not worth
    raising over, because the job's real work is already decided by this point.
    """
    reason = "; ".join(failures or [])
    note = (
        "I could not write that into your plan after all"
        + (f" - {reason}." if reason else ".")
        + " Your schedule is unchanged. Tell me to try again and I will, or we "
        "can change what we are asking for."
    )
    try:
        from app.services.coach import threads as thread_service

        thread_service.record_coach_note(db, thread_id, note)
    except Exception:  # noqa: BLE001 - the note is the consolation, not the work
        logger.exception("schedule amend: failure note not written to %s", thread_id)


def _record_in_thread(db, thread_id, description: str | None, changes=None) -> None:
    """Leave the ledger entry for a change that has now actually been made.

    It records what the amendment DID, not what the card asked for. The card is
    minted before the generation runs, so it can only forecast; when the two
    differ the ledger has to carry the truth, because it is what the coach reads
    back as "already in their record". A live amendment differed silently: the
    card promised an easy run would become hill reps, and the rewrite removed the
    week's interval session instead.

    The card's wording is kept as the opening line, because that is what the
    runner agreed to and it is how they recognise the entry.

    Fail-soft, the `_record_confirmed` posture it replaces: the sessions are
    written and the runner can see them, so losing the trace is a smaller harm
    than a job that raises. A job enqueued before this shipped carries neither
    argument and simply records nothing.
    """
    if not thread_id or not description:
        return
    if changes:
        description = description.rstrip() + " | " + " ".join(changes)
    try:
        from app.services.coach import threads as thread_service

        thread_service.record_action_event(db, uuid.UUID(str(thread_id)), None, description)
    except Exception:  # noqa: BLE001 - the change is made; the trace is not worth a raise
        logger.exception("schedule amend: trace write failed for thread %s", thread_id)


def enqueue_amendment(
    user_id,
    plan_id,
    *,
    weeks_from: int,
    weeks_through: int,
    instruction: str,
    thread_id=None,
    description: str | None = None,
) -> None:
    """Enqueue the amendment, decoupled from the confirm request.

    The `enqueue_draft` idiom: the queue dependency stays off the read
    endpoints' import path, and a Redis hiccup leaves the runner's plan untouched
    rather than 500ing a confirm that has already spent its token.
    """
    try:
        from app.core.queue import queue

        queue.enqueue(
            amend_schedule_job,
            str(user_id),
            str(plan_id),
            int(weeks_from),
            int(weeks_through),
            instruction or "",
            str(thread_id) if thread_id else None,
            description or None,
        )
    except Exception:  # noqa: BLE001 - enqueue is fire-and-forget
        logger.exception("failed to enqueue schedule amendment for plan %s", plan_id)
