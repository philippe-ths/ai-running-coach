import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Float, Integer, ForeignKey, DateTime, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class DerivedMetric(Base):
    __tablename__ = "derived_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"), unique=True)

    activity_class: Mapped[str] = mapped_column(String)  # Interval, Tempo, Long, etc.
    effort_score: Mapped[float] = mapped_column(Float)
    pace_variability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hr_drift: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    time_in_zones: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    stops_analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    efficiency_analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=[])  # list[str]
    confidence: Mapped[str] = mapped_column(String)  # low, medium, high
    confidence_reasons: Mapped[list] = mapped_column(JSON, default=[])
    interval_structure: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    workout_match: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    interval_kpis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_reasons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    activity = relationship("Activity", back_populates="metrics")
