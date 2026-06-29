"""Runner memory update RQ job (M2, ADR 0025) — rewrite a runner's memory profile.

Enqueued (decoupled) after each non-fallback exchange under a memory-aware prompt
(the same seam where the legacy narrative consolidation fired). Opens its own DB
session like the other jobs in this package and runs the async rewrite-from-source
pass to completion. The user-facing exchange has already returned by the time this
runs, so a slow Haiku call here never blocks the runner's report, and a failure is
swallowed so a background hiccup never crashes the worker.
"""

import asyncio
import logging

from app.db.session import SessionLocal
from app.services.coach.memory_update import update_memory

logger = logging.getLogger(__name__)


def update_memory_job(user_id: str) -> None:
    """RQ entrypoint. Rewrites the user's memory profile from current sources."""
    db = SessionLocal()
    try:
        asyncio.run(update_memory(db, user_id))
    except Exception:  # noqa: BLE001 — background work, never crash the worker
        logger.exception("update_memory_job failed for user %s", user_id)
    finally:
        db.close()
