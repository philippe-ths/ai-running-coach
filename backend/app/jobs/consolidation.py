"""Consolidation RQ job (A2c) — re-write a runner's durable-memory narrative.

Enqueued (decoupled) after each non-fallback exchange, at the same seam where the
deterministic belief write-back fires. Opens its own DB session, like the other
jobs in this package, and runs the async consolidation to completion. The
user-facing exchange has already returned by the time this runs, so a slow Haiku
call here never blocks the runner's report.
"""

import asyncio
import logging

from app.db.session import SessionLocal
from app.services.coach.consolidation import consolidate_narrative

logger = logging.getLogger(__name__)


def consolidate_narrative_job(user_id: str) -> None:
    """RQ entrypoint. Re-grounds the user's narrative from current facts."""
    db = SessionLocal()
    try:
        asyncio.run(consolidate_narrative(db, user_id))
    except Exception:  # noqa: BLE001 — background work, never crash the worker
        logger.exception("consolidate_narrative_job failed for user %s", user_id)
    finally:
        db.close()
