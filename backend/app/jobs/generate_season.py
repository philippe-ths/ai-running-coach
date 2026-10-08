"""The RQ job that plans a runner's season (#1064).

Runner-triggered, never scheduled, and never crashes the worker: the endpoint
creates the `drafting` row and enqueues this, so a failed generation leaves a
`failed` row the client can report. The module path is a deploy contract (RQ
serializes the job as `module.function`).
"""

import asyncio
import logging
import uuid

from app.db.session import SessionLocal
from app.models.season import Season
from app.models.user import User
from app.services.schedule import season_store
from app.services.schedule.season import generate_season

logger = logging.getLogger(__name__)


def generate_season_job(
    user_id: str, season_id: str, thread_id: str | None = None
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uuid.UUID(str(user_id))).first()
        season = db.query(Season).filter(Season.id == uuid.UUID(str(season_id))).first()
        if user is None or season is None:
            logger.warning("season: user %s or season %s is gone", user_id, season_id)
            return
        if season.user_id != user.id:
            # Cannot happen through the endpoint; checked because the two ids
            # arrive as separate job arguments.
            logger.error("season: %s does not belong to %s", season_id, user_id)
            return
        if season.status != season_store.DRAFTING:
            # Expired as stale, or already handled: do not spend tokens on a row
            # nobody is waiting for.
            logger.warning("season: %s is %s, not drafting; skipping", season_id, season.status)
            return

        outcome = asyncio.run(generate_season(db, user, season, thread_id=thread_id))
        if outcome.ok:
            logger.info("season: %s is now active", season.id)
    except Exception:
        logger.exception("season: job failed for %s", season_id)
        try:
            db.rollback()
            season = db.query(Season).filter(Season.id == uuid.UUID(str(season_id))).first()
            if season is not None and season.status == season_store.DRAFTING:
                season_store.fail_season(db, season, season_store.FAILURE_MESSAGE)
        except Exception:
            logger.exception("season: could not mark %s failed", season_id)
    finally:
        db.close()
