import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, DateTime, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CheckIn(Base):
    __tablename__ = "check_ins"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"))

    rpe: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pain_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pain_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sleep_quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    activity = relationship("Activity", back_populates="check_in")
