"""Memory store (M1, ADR 0025) — thin DB layer over the `runner_memory` table.

The runner memory profile: one rewritten-from-source per-runner profile, written
by the background update pass (M2) and read into the coach pack (M3). This layer
only persists and retrieves the row and renders it to markdown; the authority
boundary (memory yields to measured data, never lowers the safety floor) is
enforced elsewhere (the v13 prompt addendum + the medical-scope validator). No
LLM here.

Three entry points:
- `get_memory(db, user_id)`: the user's memory row, or None.
- `upsert_memory(db, user_id, ...)`: create or wholesale-replace the single
  per-user row (the update pass rewrites it from scratch each run, so this is a
  full replace, not a merge). Best-effort against the UNIQUE(user_id) race.
- `render_profile_markdown(profile)`: a pure projection of a profile to markdown
  for surfacing/debug — emits all five headings even when a section is empty.

All reads/writes are user-scoped.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.runner_memory import RunnerMemory
from app.schemas.coach_memory import (
    MEMORY_SECTION_FIELDS,
    MEMORY_SECTION_TITLES,
    RunnerMemoryProfile,
)

logger = logging.getLogger(__name__)


def _as_uuid(value) -> uuid.UUID:
    """Accept a UUID or its string form (SQLite, used in tests, won't implicitly
    cast a string to a UUID column the way Postgres does)."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def get_memory(db: Session, user_id) -> Optional[RunnerMemory]:
    """The user's single memory row, or None if none has been written yet."""
    return (
        db.query(RunnerMemory)
        .filter(RunnerMemory.user_id == _as_uuid(user_id))
        .first()
    )


def upsert_memory(
    db: Session,
    user_id,
    *,
    profile: Optional[RunnerMemoryProfile],
    model_id: Optional[str] = None,
    source_report_count: Optional[int] = None,
    grounded_through: Optional[datetime] = None,
) -> Optional[RunnerMemory]:
    """Create or wholesale-replace the user's memory row.

    The update pass re-derives the profile from scratch each run (so it
    self-corrects against the sources rather than drifting), so this is a full
    replace of the profile + provenance, not a merge. Guarded against the
    UNIQUE(user_id) race: a concurrent insert loses cleanly rather than stacking
    a second row.
    """
    uid = _as_uuid(user_id)
    serialized = profile.model_dump() if profile is not None else None

    row = db.query(RunnerMemory).filter(RunnerMemory.user_id == uid).first()
    created = row is None
    if created:
        row = RunnerMemory(user_id=uid)
        db.add(row)

    row.profile = serialized
    row.model_id = model_id
    row.source_report_count = source_report_count
    row.grounded_through = grounded_through

    try:
        db.commit()
    except IntegrityError:
        # A concurrent update pass inserted the row first. Re-read and replace in
        # place rather than risk a duplicate.
        db.rollback()
        if created:
            row = (
                db.query(RunnerMemory)
                .filter(RunnerMemory.user_id == uid)
                .first()
            )
            if row is None:
                return None
            row.profile = serialized
            row.model_id = model_id
            row.source_report_count = source_report_count
            row.grounded_through = grounded_through
            db.commit()
        else:
            raise
    db.refresh(row)
    return row


def render_profile_markdown(profile: RunnerMemoryProfile) -> str:
    """Render a profile to markdown for surfacing/debug.

    Pure (no DB, no LLM). Always emits all five section headings in canonical
    order, even when a section is empty, so the shape is constant for every runner
    ("same sections everyone").
    """
    blocks: list[str] = []
    for field in MEMORY_SECTION_FIELDS:
        lines = [f"## {MEMORY_SECTION_TITLES[field]}"]
        items = getattr(profile, field)
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("_(nothing yet)_")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"
