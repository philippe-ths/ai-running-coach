"""RunnerMemory — the rewritten-from-source runner memory profile (M1, ADR 0025).

The durable-memory replacement for the retired belief (`CoachingContext`) +
narrative (`CoachNarrative`) loop. One row per user (identity `user_id`, UNIQUE)
holds the whole `RunnerMemoryProfile` (five capped sections) as JSON, rewritten
from scratch from the raw sources by a background update pass after each
non-fallback exchange. The writer never reads this row's prior value — it is
rewritten from source, never edited — which is what makes the anti-echo guarantee
structural (ADR 0025).

The remaining columns are provenance so a thin/stale profile can be hedged and
audited:
  - `model_id`: which model wrote this profile (provenance, not config).
  - `source_report_count`: how many source exchanges it was rewritten from.
  - `grounded_through`: the date of the most recent source it was grounded from.

Additive and backward-safe: this table is new, starts empty, and nothing reads or
writes it until the writer (M2) and reader (M3) ship and the v13 prompt is flipped.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class RunnerMemory(Base):
    __tablename__ = "runner_memory"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )

    # The whole RunnerMemoryProfile, serialized. Nullable: a row may be created
    # before its first successful update pass produces a profile.
    profile: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Provenance — what this profile was rewritten from, for hedging + audit.
    model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_report_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grounded_through: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", backref="runner_memory")
