"""The RQ job that writes a runner's period report (#946).

Runner-triggered, never scheduled — the endpoint writes the `generating` row and
enqueues this, the `app/jobs/generate_schedule.py` precedent. Never crashes the
worker: a failed generation leaves a `failed` row the runner's client can report,
which is the whole point of creating the row before generation rather than after.
"""

import asyncio
import logging
import uuid

from app.db.session import SessionLocal
from app.models.period_report import PeriodReport
from app.models.user import User
from app.services.coach import period_report_store as store
from app.services.coach.period_report import generate_period_report

logger = logging.getLogger(__name__)


def generate_period_report_job(user_id: str, report_id: str) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uuid.UUID(str(user_id))).first()
        report = (
            db.query(PeriodReport)
            .filter(PeriodReport.id == uuid.UUID(str(report_id)))
            .first()
        )
        if user is None or report is None:
            logger.warning(
                "period report: user %s or report %s is gone; nothing to do",
                user_id,
                report_id,
            )
            return
        if report.user_id != user.id:
            # Cannot happen through the endpoint, which mints the row for the
            # authenticated runner. Checked anyway: this is the one place a
            # report id and a user id arrive as separate job arguments.
            logger.error(
                "period report: report %s does not belong to %s", report_id, user_id
            )
            return

        outcome = asyncio.run(generate_period_report(db, user, report))
        if outcome.ok:
            logger.info("period report: report %s is ready", report.id)
        else:
            logger.info(
                "period report: report %s failed (%s)", report.id, outcome.failure_kind
            )
    except Exception:
        logger.exception("period report: job failed for report %s", report_id)
        try:
            report = (
                db.query(PeriodReport)
                .filter(PeriodReport.id == uuid.UUID(str(report_id)))
                .first()
            )
            if report is not None and report.status == store.GENERATING:
                store.mark_failed(
                    db, report, "the generation job raised", kind=store.FAILURE_UNKNOWN
                )
        except Exception:
            logger.exception("period report: could not mark report %s failed", report_id)
    finally:
        db.close()


def enqueue_period_report(user_id, report_id) -> None:
    """Enqueue the generation job, decoupled from the request — the
    `schedule.draft.enqueue_draft` idiom: a Redis hiccup leaves a `generating`
    row the runner can retry rather than a 500 on a request that already wrote one.
    """
    try:
        from app.core.queue import queue

        queue.enqueue(generate_period_report_job, str(user_id), str(report_id))
    except Exception:  # noqa: BLE001 — enqueue is fire-and-forget
        logger.exception("failed to enqueue period report %s", report_id)
