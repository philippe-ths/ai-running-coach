"""
UserMaterial — runner-uploaded coaching content (P4, #285, ADR 0017).

`User materials` is the first place the product accepts genuinely UNTRUSTED
input: a runner's own methodology, a human coach's plan, a physio protocol, a
race-day plan, a book passage. One uploaded markdown file is one row.

The safety design is CONTAINMENT, not detection (ADR 0017). The row keeps two
representations:
  - `raw_text`: the untrusted source of truth, pulled on demand only. It is
    NEVER placed into a coach prompt — only the distilled record is.
  - `distilled`: a compact, corpus-`School`-shaped record
    (`stance`/`principles`/`method_framing`/`emphasis_hints`) produced on
    ingestion by one hardcoded `claude-haiku-4-5`, structured-output-only call.
    Because the distiller emits structured output only, an injection payload can
    at worst land its words INSIDE a corpus-shaped field, where downstream
    governance (prompt rule 28, validator rule 8 — both slice 2) treats it as
    reference-data-not-instructions.

`status` is the ingestion lifecycle: `processing` (uploaded, distillation
queued) -> `active` (distilled, ready) or `failed` (distillation errored, a
visible state, never a crash); `archived` is a runner soft-hide. `content_hash`
(sha256 of the raw text) dedups identical re-uploads and detects content change.
Provenance (`distill_model`, `distilled_at`) records what produced the record.

Per-`user_id` scope from day one, forward-compatible with the ADR 0005 multi-user
plan. Many rows per user (the FK is indexed, NOT unique). Additive and
backward-safe: this table is new, starts empty, and nothing else depends on it.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class UserMaterial(Base):
    __tablename__ = "user_materials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # Runner-set light label (philosophy|plan|protocol|race_plan|other). Steers
    # the distiller's framing and the runner's own organisation; no downstream
    # behavioural branching (ADR 0017 fork 4). Stored as String, like the other
    # enum-ish columns in this codebase; the allowed set is validated at the API.
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)

    # The untrusted source of truth. Pull-on-demand only; NEVER enters a prompt.
    raw_text: Mapped[str] = mapped_column(Text)

    # The compact corpus-`School`-shaped distilled record. Null until the
    # background distiller succeeds; the only thing that ever reaches an exchange.
    distilled: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Ingestion lifecycle: processing -> active | failed; archived = soft-hide.
    status: Mapped[str] = mapped_column(String, default="processing")

    # sha256 of raw_text — dedup of identical re-uploads + content-change detection.
    content_hash: Mapped[str] = mapped_column(String, index=True)

    # Provenance of the distilled record (which model, when), for audit + hedging.
    distill_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    distilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )

    user = relationship("User", backref="materials")
