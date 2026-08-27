"""The RQ job that drafts a runner's training plan (#830).

Runner-triggered, never scheduled: the endpoint creates the `drafting` row and
enqueues this, so no background pass ever spends tokens writing plans for runners
who have not asked for one.

Like every other background coach job here it never crashes the worker — a failed
draft leaves a `failed` plan row the runner's client can report, which is the
whole point of creating the row before the generation rather than after.
"""

import asyncio
import logging
import uuid

from app.db.session import SessionLocal
from app.models.user import User
from app.services.schedule import store
from app.services.schedule.draft import draft_plan

logger = logging.getLogger(__name__)


def generate_schedule_job(
    user_id: str,
    plan_id: str,
    thread_id: str | None = None,
    description: str | None = None,
) -> None:
    """Draft one plan. `thread_id` (#856) names the conversation that settled it.

    `description` is the confirm card's own wording, carried so the ledger entry
    is written HERE, once the plan actually exists (#778's contract: the ledger
    records writes, not taps). Recording it at confirm time recorded an intention
    as an outcome, and drafting happens on the worker a minute later, if at all.

    Both are optional and trailing, so a job already sitting in Redis when this
    deploys — RQ serialized it with two or three arguments — still binds and runs
    the path it was enqueued for.
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
                "schedule draft: user %s or plan %s is gone; nothing to do",
                user_id,
                plan_id,
            )
            return
        if plan.user_id != user.id:
            # Cannot happen through the endpoint, which mints the row for the
            # authenticated runner. Checked anyway: this is the one place a plan
            # id and a user id arrive as separate arguments.
            logger.error("schedule draft: plan %s does not belong to %s", plan_id, user_id)
            return

        outcome = asyncio.run(draft_plan(db, user, plan, thread_id=thread_id))
        if outcome.ok:
            logger.info("schedule draft: plan %s is now active", plan.id)
            _record_in_thread(db, thread_id, description)
        else:
            store.fail_plan(
                db,
                plan,
                "; ".join(outcome.failures or ["unknown"]),
                # The category the draft itself decided (#859). Classified where
                # the failure is known rather than re-derived from the joined
                # prose here, which would be string-matching text written for a
                # rewrite prompt.
                kind=outcome.failure_kind,
            )
    except Exception:
        logger.exception("schedule draft: job failed for plan %s", plan_id)
        try:
            plan = (
                db.query(store.TrainingPlan)
                .filter(store.TrainingPlan.id == uuid.UUID(str(plan_id)))
                .first()
            )
            if plan is not None and plan.status == store.DRAFTING:
                store.fail_plan(
                    db,
                    plan,
                    "the drafting job raised",
                    kind=store.FAILURE_UNKNOWN,
                )
        except Exception:
            logger.exception("schedule draft: could not mark plan %s failed", plan_id)
    finally:
        db.close()


def _record_in_thread(db, thread_id, description: str | None) -> None:
    """Leave the ledger entry for a plan that now actually exists.

    Fail-soft: the plan is written and the runner can see it on their screen, so
    losing the trace is a smaller harm than a job that raises. A job enqueued
    before this shipped carries no description and simply records nothing.
    """
    if not thread_id or not description:
        return
    try:
        from app.services.coach import threads as thread_service

        thread_service.record_action_event(
            db, uuid.UUID(str(thread_id)), None, description
        )
    except Exception:  # noqa: BLE001 - the plan is written; the trace is not worth a raise
        logger.exception("schedule draft: trace write failed for thread %s", thread_id)
