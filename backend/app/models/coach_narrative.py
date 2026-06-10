"""
CoachNarrative — the LLM narrative half of durable memory (A2c).

Durable memory is split into two authorities (ADR 0008):
  - deterministic facts (beliefs in `CoachingContext`, norms in `RunnerBaseline`,
    derived preferences) — authoritative for grounding, never LLM-written; and
  - this LLM narrative — the bounded, per-runner relationship story, authoritative
    for VOICE only.

The boundary between them is ABSOLUTE and mirrors the standing belief rule: the
narrative can never override a re-derived `DerivedMetric` or a deterministic
fact, and can never be the cited source of a factual claim. It is colour, not
fact. A background `Consolidation` job re-writes it from the deterministic facts
plus recent exchange digests after each non-fallback exchange, so it self-corrects
against ground truth instead of drifting (it is decoupled — the user-facing
exchange never waits on it).

One row per user (identity `user_id`, UNIQUE). The text is the only thing the
job writes; the remaining columns are provenance so a stale or thinly-grounded
narrative can be hedged and audited:
  - `model_id`: which model wrote this narrative (provenance, not config).
  - `source_report_count`: how many exchange digests it was grounded from.
  - `grounded_through`: the date of the most recent exchange it was grounded from
    (so a consumer knows how fresh the story is).

Additive and backward-safe: this table is new, starts empty, and nothing depends
on it. A runner with no narrative row yet simply has no narrative in their pack,
exactly as a runner with no beliefs has no believed_facts.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachNarrative(Base):
    __tablename__ = "coach_narratives"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # The bounded relationship story. Voice only, never fact. Nullable: a row may
    # be created before its first successful consolidation, or a consolidation may
    # legitimately produce nothing yet.
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Provenance — what this narrative was grounded from, for hedging + audit.
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

    user = relationship("User", backref="coach_narrative")
