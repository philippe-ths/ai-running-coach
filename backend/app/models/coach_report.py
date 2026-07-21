import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, DateTime, Index, JSON, String, Uuid, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.db.base import Base
from app.models.base import generate_uuid


class CoachReport(Base):
    __tablename__ = "coach_reports"

    # Cache identity is (activity_id, prompt_id, schema_version): one CURRENT report
    # per activity per report shape. A version bump retains prior versions rather
    # than overwriting them, so report history is comparable across prompt/schema
    # changes (the seam the M5 eval harness relies on). prompt_id/schema_version
    # are nullable so old code (during the deploy window on the preview-shared
    # production DB) can still insert; new code always populates them.
    #
    # #646 non-destructive regen: a force regeneration no longer overwrites the
    # active row in place. It sets `superseded_at` on the prior current row (an
    # immutable audit copy of what the coach saw) and inserts the regenerated report
    # as a new CURRENT row. "Current" = superseded_at IS NULL, so the uniqueness is
    # a PARTIAL unique index scoped to the current rows (unlimited archived copies,
    # exactly one current row per key). Expressed for both Postgres (prod) and SQLite
    # (tests build from the model via create_all); both support partial indexes.
    __table_args__ = (
        Index(
            "uq_coach_reports_activity_version_current",
            "activity_id", "prompt_id", "schema_version",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
            sqlite_where=text("superseded_at IS NULL"),
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

    # #646 non-destructive regeneration: NULL for the CURRENT report, a timestamp for
    # an ARCHIVED (superseded) audit copy. A force "Re-run" sets this on the prior
    # current row and inserts a fresh current row, so the original report + its
    # point-in-time context_pack snapshot survive for comparison instead of being
    # overwritten. All display/active/digest/eval read paths filter to superseded_at
    # IS NULL; archived rows are audit-only and never served as the report.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # P1.1 voice freshness (not cache IDENTITY): the fingerprint of the runner's
    # declared voice this report was generated under, so a later voice change is
    # detectable and the report can regenerate onto the new voice. Deliberately NOT
    # in the unique constraint — there is still one row per (activity, prompt,
    # schema); voice_key only records which voice that row currently speaks in.
    # Null for a report generated under a non-voice-aware prompt (voice is inert) and
    # for rows written before this column existed; a null on an active voice-aware
    # row reads as stale, so it regenerates once onto the current voice.
    voice_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activity = relationship("Activity", backref="coach_report")
