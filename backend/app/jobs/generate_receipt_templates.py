"""Background job: regenerate a runner's voiced receipt templates (#296).

Enqueued when the runner changes their Voice (PUT /api/coach/voice) and lazily
when a receipt finds none stored. Runs the structured-output-only generation +
validation OFFLINE (never on the receipt hot path), so the receipt stays instant
and the untrusted-freetext-derived set is validated before it can ever be sent.
Never crashes the worker: refresh_receipt_templates is fail-visible.
"""

import asyncio
import logging

from app.db.session import SessionLocal
from app.services.coach.receipt_voice import refresh_receipt_templates

logger = logging.getLogger(__name__)


def generate_receipt_templates_job(user_id: str) -> None:
    """RQ entrypoint. Opens its own session and refreshes the runner's voiced
    receipt templates from their current declared voice."""
    db = SessionLocal()
    try:
        asyncio.run(refresh_receipt_templates(db, user_id))
    except Exception:  # noqa: BLE001 — fail-visible; a generation failure is not a crash
        logger.exception("receipt-template generation job failed for user %s", user_id)
    finally:
        db.close()
