"""
Exchange — the first-class two-stage coaching exchange for one Block (A1,
ADR 0011). Owns the lifecycle state and the per-stage at-most-once notification
sentinels that A4 parked on `Activity` (`opener_notification_sent_at`,
`coach_notification_sent_at`); those columns stop being written but are
retained until a separately-approved delete.

One exchange per block, strictly: `block_id` is UNIQUE because a closed
exchange is never re-opened — a late activity arriving after the fuller has
been sent starts a new block with its own exchange.

`coach_reports` is untouched by this: it stays the versioned generation
artifact keyed `(activity_id, prompt_id, schema_version)` on the block's
primary activity.

Sentinel semantics mirror A4 exactly: a sentinel is set only on successful
send, left null on failure so the stage stays re-sendable, and never reset by
a `force=true` regeneration.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class Exchange(Base):
    __tablename__ = "exchanges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # `unique=True` alone, deliberately (#839): with `index=True` alongside it
    # SQLAlchemy emits a UNIQUE INDEX, while the creating migration
    # (c4d5e6f7a8b9) emitted a UNIQUE CONSTRAINT -- the same guarantee spelled
    # two ways, which made `alembic check` permanently red and unable to guard
    # real drift. Postgres backs a unique constraint with a btree index, so the
    # lookup `index=True` was there for is already served.
    block_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blocks.id"), unique=True)

    # Lifecycle: set when the opener stage has GENERATED (the exchange has
    # opened and spoken for the block), independent of notification delivery —
    # so the reply path works under a NoOp local notifier exactly as the A4
    # report-shape probe did. The reply window is anchored here.
    opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Stage sentinels (the A4 Activity sentinels, relocated). opener_sent_at is
    # the opener-notification dedup; fuller_sent_at is the fuller-notification
    # dedup AND the fuller job's idempotency guard. fuller_sent_at set means the
    # exchange is CLOSED.
    opener_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fuller_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The receipt cadence (#296). `done_at` records the runner tapping "done" on a
    # receipt — the explicit "this session is finished" signal that schedules the
    # full report (idempotent with the block-quiet timer; both target +BLOCK_GAP).
    # Under the receipt cadence `opener_sent_at` is unused (there is no LLM opener —
    # the per-activity receipt opens the exchange via `opened_at` and dedups on the
    # Activity), and `fuller_sent_at` keeps its meaning as the CLOSED sentinel (set
    # when the full report has been sent). Nullable, zero-backfill.
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    block = relationship("Block", backref="exchange_row", uselist=False)
