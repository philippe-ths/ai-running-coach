"""Pull recent nights from a recovery source into the store (#555)."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.recovery import store
from app.services.recovery.port import RecoveryAuthError, RecoverySource

logger = logging.getLogger(__name__)

# Daily runs re-fetch the last few nights because a device syncs late: a night
# first seen at 7am may only gain its HRV or score after the watch uploads.
DAILY_LOOKBACK_DAYS = 3


@dataclass
class SyncResult:
    written: int = 0
    skipped_empty: int = 0
    failed: int = 0
    auth_failed: bool = False


def sync_recent(
    db: Session, user_id, source: RecoverySource, *, today: date, days: int
) -> SyncResult:
    """Fetch the nights ``today - days + 1 .. today`` and upsert each.

    One bad day never loses the others: a transient failure is counted and the
    loop moves on. An auth failure stops the run at once, because every remaining
    day would fail identically and hammer the provider with a dead token. A night
    with no signal at all is not written (nothing to store, and an empty row would
    read as a measured night).
    """
    result = SyncResult()
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        try:
            reading = source.fetch_day(day)
        except RecoveryAuthError:
            logger.error(
                "recovery_sync auth failed for source=%s; stopping (re-mint the token)",
                source.source,
            )
            result.auth_failed = True
            break
        except Exception as exc:  # noqa: BLE001 - one day's failure must not lose the rest
            logger.warning(
                "recovery_sync fetch failed source=%s day=%s: %s",
                source.source,
                day,
                type(exc).__name__,
            )
            result.failed += 1
            continue
        if not reading.has_signal():
            result.skipped_empty += 1
            continue
        store.upsert_reading(db, user_id, source.source, reading)
        result.written += 1
    db.commit()
    return result
