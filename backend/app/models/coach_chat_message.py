import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, DateTime, String, Text, Uuid
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activity = relationship("Activity", backref="chat_messages")
