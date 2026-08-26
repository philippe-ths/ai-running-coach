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
            _record_in_thread(db, thread_id, description)
        else:
            logger.warning(
                "schedule amend: plan %s unchanged (%s): %s",
                plan.id,
                outcome.failure_kind,
                "; ".join(outcome.failures or ["unknown"]),
            )
    except Exception:
        logger.exception("schedule amend: job failed for plan %s", plan_id)
    finally:
        db.close()


def _record_in_thread(db, thread_id, description: str | None) -> None:
    """Leave the ledger entry for a change that has now actually been made.

    Fail-soft, the `_record_confirmed` posture it replaces: the sessions are
    written and the runner can see them, so losing the trace is a smaller harm
    than a job that raises. A job enqueued before this shipped carries neither
    argument and simply records nothing.
    """
    if not thread_id or not description:
        return
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
