"""Idempotent writes and owner-scoped reads for `RecoveryDay` (#555)."""

from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import RecoveryDay
from app.services.recovery.port import RecoveryReading

_FIELDS = (
    "sleep_duration_s",
    "sleep_score",
    "hrv_avg_ms",
    "hrv_status",
    "hrv_baseline_low_ms",
    "hrv_baseline_high_ms",
    "resting_hr",
)


def upsert_reading(
    db: Session,
    user_id,
    source: str,
    reading: RecoveryReading,
    *,
    now: Optional[datetime] = None,
) -> RecoveryDay:
    """Write one night, replacing any earlier row for (user, day, source).

    Replaces every signal, including with None: a corrected fetch that no longer
    carries a value is the device's current word, and keeping the stale number
    would present a withdrawn measurement as fact. Does not commit.
    """
    fetched_at = now or datetime.now(timezone.utc)
    row = (
        db.query(RecoveryDay)
        .filter(
            RecoveryDay.user_id == user_id,
            RecoveryDay.day == reading.day,
            RecoveryDay.source == source,
        )
        .first()
    )
    if row is None:
        row = RecoveryDay(user_id=user_id, day=reading.day, source=source)
        db.add(row)
    for name in _FIELDS:
        setattr(row, name, getattr(reading, name))
    row.fetched_at = fetched_at
    return row


def list_recent(db: Session, user_id, *, since: date) -> List[RecoveryDay]:
    """The runner's own nights from ``since`` on, newest first."""
    return (
        db.query(RecoveryDay)
        .filter(RecoveryDay.user_id == user_id, RecoveryDay.day >= since)
        .order_by(RecoveryDay.day.desc(), RecoveryDay.source.asc())
        .all()
    )


def has_any(db: Session, user_id, source: str) -> bool:
    return (
        db.query(RecoveryDay.id)
        .filter(RecoveryDay.user_id == user_id, RecoveryDay.source == source)
        .first()
        is not None
    )
