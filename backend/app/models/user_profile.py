import uuid
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, DateTime, Date, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)

    goal_type: Mapped[str] = mapped_column(String)  # 5k, marathon, general
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    experience_level: Mapped[str] = mapped_column(String)  # new, intermediate, advanced
    weekly_days_available: Mapped[int] = mapped_column(Integer)
    current_weekly_km: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_hr_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "user_entered", "race_estimate", "lab_test"
    upcoming_races: Mapped[list] = mapped_column(JSON, default=[])  # List[dict]
    injury_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", back_populates="profile")
