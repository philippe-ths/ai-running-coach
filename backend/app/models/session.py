import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Uuid, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class Session(Base):
    """A server-side session backing a signed-out-able login (ADR 0005).

    The browser cookie carries a random opaque token; only its SHA-256 hash is
    stored, so the row is the source of truth and a session can be revoked by
    deleting it (used by logout and account deletion). A session is valid only
    while a matching, unexpired row exists.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
