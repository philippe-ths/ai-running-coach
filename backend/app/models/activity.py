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

    # The Block this activity belongs to (A1, ADR 0011). Nullable: an activity
    # is assigned at ingestion (or by backfill); a null means not yet grouped.
    block_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("blocks.id", use_alter=True), nullable=True, index=True
    )

    # The FULLER-turn notification sentinel (A4): set once the fuller coaching turn
    # has been notified. Also the fuller job's idempotency guard, so a reply-fired
    # and a timer-fired fuller run cannot double-send. (Pre-A4 this was the single
    # coach-report notification sentinel; under a single-shot prompt it keeps that
    # meaning.) Left null on send failure so the activity stays re-sendable.
    coach_notification_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The OPENER notification sentinel (A4 two-stage Exchange): set once the
    # lightweight opener has been notified. Per-stage dedup, mirroring
    # coach_notification_sent_at — re-generating a report (force=true) never resets
    # it, so an opener notification fires at most once per activity. Null until the
    # opener notifies; left null on send failure.
    opener_notification_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The per-activity receipt sentinel (#296 receipt cadence): set once the instant
    # deterministic receipt for THIS activity has been notified. Receipts are
    # per-activity (one per activity is acceptable, owner decision) while the exchange
    # is per-block, so dedup lives here on the Activity, mirroring the retired A4
    # opener_notification_sent_at. Left null on send failure so the receipt stays
    # re-sendable; never reset by a regeneration. Null under the prior cadences.
    receipt_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set once this activity's coach report has contributed its belief deltas to
    # the M8 CoachingContext store. The at-most-once-per-activity sentinel (like
    # coach_notification_sent_at) so a re-read or force-regeneration of the report
    # never re-counts the same physical run's observations.
    beliefs_written_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set once the historical stream-analysis backfill (#110) has attempted this
    # activity, regardless of whether Strava had streams for it. Combined with
    # the presence of `streams` rows, it makes the backfill resumable and
    # convergent: each summary-only activity is attempted exactly once.
    streams_backfilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
    block = relationship("Block", back_populates="activities", foreign_keys=[block_id])
