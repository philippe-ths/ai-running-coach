import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, DateTime, JSON, String, Uuid, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachReport(Base):
    __tablename__ = "coach_reports"

    # Cache identity is (activity_id, prompt_id, schema_version): one report per
    # activity per report shape. A version bump retains prior versions rather
    # than overwriting them, so report history is comparable across prompt/schema
    # changes (the seam the M5 eval harness relies on). prompt_id/schema_version
    # are nullable so old code (during the deploy window on the preview-shared
    # production DB) can still insert; new code always populates them.
    __table_args__ = (
        UniqueConstraint(
            "activity_id", "prompt_id", "schema_version",
            name="uq_coach_reports_activity_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"))
    prompt_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schema_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    report: Mapped[dict] = mapped_column(JSON)
    meta: Mapped[dict] = mapped_column(JSON)
    context_pack: Mapped[dict] = mapped_column(JSON)
    raw_llm_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Exchange digest (A2a): the token-bounded projection of this report
    # (activity_date, headline, lead_argument, next-steps/commitments), stored so
    # later exchanges retrieve it instead of re-projecting from the full report
    # JSON. Nullable: null for fallback reports and for rows written before A2a.
    digest: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    is_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activity = relationship("Activity", backref="coach_report")
