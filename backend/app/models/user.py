import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    # Phase 2 (ADR 0022): the verified email is the durable identity key, so it
    # is non-null and unique. The pre-Phase-2 single user was backfilled to a
    # placeholder by migration a9d4f2c7e1b6 and reconciled to the owner's real
    # email on first sign-in (app/core/clerk_auth.resolve_user_by_email).
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # Per-user notification routing (P2.4, #120, ADR 0023): the user's bound
    # Telegram chat. Null until they link; an unbound user falls back to the
    # configured global recipient (single-user back-compat).
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strava_account = relationship("StravaAccount", back_populates="user", uselist=False)
    activities = relationship("Activity", back_populates="user")
    profile = relationship("UserProfile", back_populates="user", uselist=False)
