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
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strava_account = relationship("StravaAccount", back_populates="user", uselist=False)
    activities = relationship("Activity", back_populates="user")
    profile = relationship("UserProfile", back_populates="user", uselist=False)
