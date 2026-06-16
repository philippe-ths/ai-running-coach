"""Material distillation RQ job (P4, #285).

Enqueued by the materials upload endpoint right after the raw text is stored. Opens
its own DB session, like the other jobs in this package, and runs the async distiller
to completion. The upload request has already returned 202 by the time this runs, so
a slow Haiku call here never blocks the runner's upload, and a failure is recorded as
status=failed (by the distiller) rather than crashing the worker.
"""

import asyncio
import logging

from app.db.session import SessionLocal
from app.services.coach.material_distiller import distill_material

logger = logging.getLogger(__name__)


def distill_material_job(material_id: str) -> None:
    """RQ entrypoint. Distils one uploaded material in place."""
    db = SessionLocal()
    try:
        asyncio.run(distill_material(db, material_id))
    except Exception:  # noqa: BLE001 — background work, never crash the worker
        logger.exception("distill_material_job failed for material %s", material_id)
    finally:
        db.close()
