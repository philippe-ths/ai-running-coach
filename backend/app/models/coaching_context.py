"""
CoachingContext — the durable, user-scoped, write-gated belief store (M8).

The architectural heart of the moat: instead of the CoachReport history being a
pile of artifacts, each report writes back small structured belief deltas
(confirmed confounds like "this runner's HR reads inflated in heat", adherence
patterns like "responds to easy-day discipline") into this store, and the next
report reads them. Beliefs are deterministically extracted by the pipeline (never
free LLM text, so they stay auditable), written through gates (non-redundant,
quality-thresholded, contradiction-resolved, not-every-run), and tiered by a TTL
so stale facts decay out of retrieval while reinforced ones persist.

One row per (user, belief). A belief's identity is (user_id, kind, key), enforced
by a DB UNIQUE constraint so a concurrent write can never stack two contradictory
rows for the same belief; reinforcement updates that one row IN PLACE (so there
is no per-observation audit history, by design). Additive and backward-safe: this
table is new and starts empty, and no other table depends on it.

This store NEVER overrides the re-derived DerivedMetric ground truth (forbidden
by design); it only carries prior context the coach applies, hedged by the
confidence/recency tags it is retrieved with, and a condition-scoped belief (an
HR confound) is only surfaced on a run that shares its triggering condition.
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachingContext(Base):
    __tablename__ = "coaching_context"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "key", name="uq_coaching_context_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # Semantic class of the belief (e.g. "hr_confound", "adherence_pattern").
    # Drives the TTL tier and, with `key`, the dedup/reinforcement identity.
    kind: Mapped[str] = mapped_column(String)
    # Identity within a kind (e.g. "heat", "easy_discipline").
    key: Mapped[str] = mapped_column(String)

    # Structured payload. Always carries a human-readable "statement" the pack
    # surfaces, plus kind-specific bookkeeping (e.g. adherence acted/total counts).
    value: Mapped[dict] = mapped_column(JSON, default=dict)

    confidence: Mapped[str] = mapped_column(String, default="low")  # low|medium|high
    # How many times this belief has been observed/reinforced; the quality gate
    # withholds a belief from retrieval until it has been seen enough times.
    observation_count: Mapped[int] = mapped_column(Integer, default=1)

    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Recency anchor for TTL/decay; bumped on every reinforcement.
    last_reinforced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", backref="coaching_context")
