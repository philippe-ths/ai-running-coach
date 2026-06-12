"""
Block — deterministic time-gap grouping of temporally-contiguous activities
into one training event (A1, ADR 0011). The coach speaks once per block: the
exchange lifecycle hangs off the block, not the activity. Grouping is
deterministic and auditable; the runner can correct it by split/merge, which
sets `user_corrected` and never re-fires a notification.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class Block(Base):
    __tablename__ = "blocks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # The activity the coach speaks about (and the coach_reports cache key):
    # the run, else the longest member. Plain FK without a relationship to
    # avoid an ambiguous join against the members' activities.block_id.
    primary_activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activities.id"), index=True
    )

    # Audit bit: set when a runner split/merge correction produced or
    # reshaped this block.
    user_corrected: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activities = relationship(
        "Activity", back_populates="block", foreign_keys="Activity.block_id"
    )
