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
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blocks.id"), unique=True, index=True
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    block = relationship("Block", backref="exchange_row", uselist=False)
