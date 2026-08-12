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
    user_id: str, plan_id: str, thread_id: str | None = None
) -> None:
    """Draft one plan. `thread_id` (#856) names the conversation that settled it.

    Optional and last, so a job already sitting in Redis when this deploys — RQ
    serialized it with two arguments — still binds and runs the unseeded path it
    was enqueued for.
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
        else:
            store.fail_plan(db, plan, "; ".join(outcome.failures or ["unknown"]))
    except Exception:
        logger.exception("schedule draft: job failed for plan %s", plan_id)
        try:
            plan = (
                db.query(store.TrainingPlan)
                .filter(store.TrainingPlan.id == uuid.UUID(str(plan_id)))
                .first()
            )
            if plan is not None and plan.status == store.DRAFTING:
                store.fail_plan(db, plan, "the drafting job raised")
        except Exception:
            logger.exception("schedule draft: could not mark plan %s failed", plan_id)
    finally:
        db.close()
