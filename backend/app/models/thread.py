import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class Thread(Base):
    """A runner-initiated, relationship-scoped conversation (ADR 0027, #765).

    Messages belong to a thread; a thread belongs to a user. The optional
    activity anchor is a framing hint — which screen context attaches at turn
    one and where the thread is listed — never a boundary on what the coach can
    discuss or fetch.
    """

    __tablename__ = "coach_threads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    activity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("activities.id"), nullable=True, index=True
    )
    # Runner-visible name; null until written (one cheap offline call after the
    # first exchange, slice 2) or the runner renames it.
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Recency for the thread switcher ordering; touched on every message write.
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", backref="threads")
    activity = relationship("Activity", backref="threads")
