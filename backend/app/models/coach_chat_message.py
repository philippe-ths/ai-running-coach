import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, DateTime, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachChatMessage(Base):
    __tablename__ = "coach_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    # #648 follow-up: the on-demand data tools the coach ran to produce this
    # assistant turn (ordered, de-duplicated tool names), so the UI can show a
    # persistent "looked up …" trace that survives a reload. Null on user turns and
    # on assistant turns that answered without a fetch.
    tools_used: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activity = relationship("Activity", backref="chat_messages")
