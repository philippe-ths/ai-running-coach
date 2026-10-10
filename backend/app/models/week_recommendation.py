"""WeekRecommendation — the recommended week the runner was last shown (#1082).

One row per runner per week. It is what lets re-planning move as little as
possible (each session's last recommended day is where it prefers to stay) and
lets the week say what moved and why, across the reads that happen between a
runner's ticks. Rewritten whenever the recommendation for the current week
changes; nothing else reads it.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class WeekRecommendation(Base):
    __tablename__ = "week_recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_week_recommendation_user_week"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    # The plan the recommendation was made for. A new plan starts the week over.
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)

    # {session_id: ISO day} as last shown.
    assignment: Mapped[dict] = mapped_column(JSON, default=dict)
    # [{session_id, title, from, to, missed}] — every move this week, oldest first.
    changes: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )
