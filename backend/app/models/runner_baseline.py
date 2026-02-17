"""
Runner baseline — rolling personal norms computed from recent training data.

Schema-only for now. Computation logic will be added in a future phase.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Float, Integer, ForeignKey, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class RunnerBaseline(Base):
    __tablename__ = "runner_baselines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)

    typical_easy_pace_s_per_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    typical_easy_hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    typical_long_run_duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    typical_weekly_distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    typical_weekly_run_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    typical_efficiency: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sample_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", backref="baseline")
