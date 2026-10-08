"""One night's recovery signals for one runner (#555).

Provider-agnostic: a row says what a device measured about the night that ended
on `day`, and `source` says which device or service said it (`garmin` today).
Nothing downstream is allowed to care which adapter produced it, so the unofficial
Garmin adapter can be replaced by the official API without touching this table.

`day` is the local calendar date the runner WOKE UP on (Garmin's `calendarDate`
for the sleep), so "last night" and "this morning" are the same row.

Every signal is nullable and means NOT MEASURED when null, never zero: a watch
left on the charger produces no HRV, and a null must not read as a bad night.
Rows are upserted per (user, day, source), so a late-arriving score replaces the
earlier partial row rather than adding a second.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import generate_uuid


class RecoveryDay(Base):
    __tablename__ = "recovery_days"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "source", name="uq_recovery_user_day_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String)

    sleep_duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sleep_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hrv_avg_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The device's own verdict against the runner's own baseline (Garmin:
    # BALANCED / UNBALANCED / LOW / POOR) and the band that verdict was judged
    # against, kept so a reader never has to guess what "low" meant.
    hrv_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    hrv_baseline_low_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hrv_baseline_high_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
