import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class LoginToken(Base):
    """A single-use magic-link token (ADR 0005).

    The raw token is emailed to the user and never stored; only its SHA-256
    hash lives here, so a database leak does not yield usable links. A token is
    spent the moment it is verified (`used_at` set) and is only valid before
    `expires_at`.
    """

    __tablename__ = "login_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
