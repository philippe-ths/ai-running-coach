"""
CoachingRelationship — the thin durable anchor of the coaching relationship
(A1, ADR 0011, owner-ratified fork). One row per user, deliberately minimal:
P1's voice/stance dials and later relationship state ALTER this table rather
than create it. Auto-created alongside the user on first profile read, the
way `UserProfile` is.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachingRelationship(Base):
    __tablename__ = "coaching_relationship"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", backref="coaching_relationship")
