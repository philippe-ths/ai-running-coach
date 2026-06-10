"""Narrative store (A2c) — thin DB layer over the `CoachNarrative` table.

The durable-memory NARRATIVE half (ADR 0008): one bounded per-runner relationship
story, VOICE ONLY, written by the background `Consolidation` job and read into the
working context's B baseline. The authority boundary is enforced elsewhere (the
prompt rule and the validator); this layer only persists and retrieves the row.

Three entry points:
- `get_narrative(db, user_id)`: the user's narrative row, or None.
- `upsert_narrative(db, user_id, ...)`: create or replace the single per-user row
  (the Consolidation job rewrites it wholesale each run, so this is a full
  replace, not a merge). Best-effort against the UNIQUE(user_id) race.
- `build_narrative_context(db, activity)`: project the row into the pack section,
  tagging recency relative to THIS run so a stale or thin story can be hedged.

All reads/writes are user-scoped (ADR-0005 multi-user readiness).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Activity
from app.models.coach_narrative import CoachNarrative
from app.schemas.coach_context import NarrativeContext

logger = logging.getLogger(__name__)


def _as_uuid(value) -> uuid.UUID:
    """Accept a UUID or its string form (SQLite, used in tests, won't implicitly
    cast a string to a UUID column the way Postgres does)."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a naive datetime (SQLite drops tzinfo) to UTC-aware so recency
    arithmetic never mixes naive and aware. No-op on Postgres."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_narrative(db: Session, user_id) -> Optional[CoachNarrative]:
    """The user's single narrative row, or None if none has been written yet."""
    return (
        db.query(CoachNarrative)
        .filter(CoachNarrative.user_id == _as_uuid(user_id))
        .first()
    )


def upsert_narrative(
    db: Session,
    user_id,
    *,
    narrative: Optional[str],
    model_id: Optional[str],
    source_report_count: Optional[int],
    grounded_through: Optional[datetime],
) -> Optional[CoachNarrative]:
    """Create or wholesale-replace the user's narrative row.

    The Consolidation job re-derives the story from scratch each run (so it
    self-corrects against the facts rather than drifting), so this is a full
    replace of the text + provenance, not a merge. Guarded against the
    UNIQUE(user_id) race the same way the belief write-back is: a concurrent
    insert loses cleanly rather than stacking a second row.
    """
    uid = _as_uuid(user_id)
    row = db.query(CoachNarrative).filter(CoachNarrative.user_id == uid).first()
    created = row is None
    if created:
        row = CoachNarrative(user_id=uid)
        db.add(row)

    row.narrative = narrative
    row.model_id = model_id
    row.source_report_count = source_report_count
    row.grounded_through = grounded_through

    try:
        db.commit()
    except IntegrityError:
        # A concurrent consolidation inserted the row first. Re-read and replace
        # in place rather than risk a duplicate.
        db.rollback()
        if created:
            row = db.query(CoachNarrative).filter(CoachNarrative.user_id == uid).first()
            if row is None:
                return None
            row.narrative = narrative
            row.model_id = model_id
            row.source_report_count = source_report_count
            row.grounded_through = grounded_through
            db.commit()
        else:
            raise
    db.refresh(row)
    return row


def build_narrative_context(db: Session, activity: Activity) -> NarrativeContext:
    """Assemble the A2c narrative pack section for an exchange anchored to
    `activity`.

    Reads the user's stored narrative and tags it with recency RELATIVE TO THIS
    RUN (`last_updated_days_ago` measured from the story's `grounded_through` to
    this activity's date) so the coach can hedge a stale or thin narrative.
    Degrades to an empty section (all None) when no narrative has been written
    yet — the coach then simply has no story to lean on, and rule 24 says nothing.
    """
    row = get_narrative(db, activity.user_id)
    if row is None or not row.narrative:
        return NarrativeContext()

    last_updated_days_ago: Optional[int] = None
    grounded = _aware(row.grounded_through)
    if grounded is not None:
        ref = _aware(activity.start_date)
        if ref is not None:
            last_updated_days_ago = max(0, (ref - grounded).days)

    return NarrativeContext(
        narrative=row.narrative,
        source_report_count=row.source_report_count,
        last_updated_days_ago=last_updated_days_ago,
    )
