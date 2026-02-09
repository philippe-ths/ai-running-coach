import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, Float, ForeignKey, DateTime, Boolean, BigInteger, JSON, Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    strava_activity_id: Mapped[int] = mapped_column(BigInteger, unique=True)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)

    distance_m: Mapped[int] = mapped_column(Integer)  # e.g. 5000
    moving_time_s: Mapped[int] = mapped_column(Integer)
    elapsed_time_s: Mapped[int] = mapped_column(Integer)
    elev_gain_m: Mapped[float] = mapped_column(Float, default=0.0)

    avg_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_speed_mps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    user_intent: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    raw_summary: Mapped[dict] = mapped_column(JSON, default={})
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", back_populates="activities")
    metrics = relationship(
        "DerivedMetric", back_populates="activity", uselist=False, cascade="all, delete-orphan"
    )
    check_in = relationship(
        "CheckIn", back_populates="activity", uselist=False, cascade="all, delete-orphan"
    )
    streams = relationship("ActivityStream", back_populates="activity", cascade="all, delete-orphan")
