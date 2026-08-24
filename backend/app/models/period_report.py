"""A period report (#946): the coach's review of a runner-chosen stretch of
training, across the disciplines the runner chose — not one activity.

Deliberately NOT `CoachReport`. That table's cache identity
(`activity_id, prompt_id, schema_version`) is a NON-NULLABLE FK to one activity,
baked into a partial unique index; a period spans many activities or none at
all, so it cannot be shoehorned into that row shape. This is its own table.

Identity mirrors the runner's own request: `user_id`, `period_start`,
`period_end` (both inclusive, local dates), the disciplines they chose, and the
`prompt_id`/`schema_version` the coach generated under — the same "what would
make this a re-request rather than a new one" question `CoachReport` answers for
a single activity. Disciplines cannot participate in an identity as a JSON list
(two lists with the same members in a different order are the same request), so
`disciplines_key` is the canonicalised, deterministic form — sorted, lower-cased,
comma-joined, `"all"` for no filter — and is what identity is actually checked
against; `disciplines` stores the runner's own strings for display.

There is no DB-level uniqueness constraint, the `TrainingPlan` precedent: "at
most one [live report] per identity" is held by the writer
(`services/coach/period_report_store.py`), not a partial index, because a
PostgreSQL partial index is syntax the SQLite test database cannot exercise —
and unlike a training plan, an OLD identity is allowed to have failed rows
sitting behind a later successful retry, so the constraint is behavioural rather
than structural.

Async lifecycle (the `TrainingPlan`/`generate_schedule_job` precedent): `status`
is `generating` -> `ready` | `failed`. The row is written `generating` before
the LLM call starts, so a crashed worker leaves a visible `generating` row a
client can report rather than silence, and `period_report_store.STALE_AFTER`
bounds how long a row may sit there before it is treated as abandoned.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class PeriodReport(Base):
    __tablename__ = "period_reports"

    __table_args__ = (
        # Not unique — see module docstring. Indexed because every read (the
        # in-flight/idempotency check, the list view) filters on this shape.
        Index(
            "ix_period_reports_identity",
            "user_id", "period_start", "period_end", "disciplines_key",
            "prompt_id", "schema_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # Both inclusive, local dates — the runner picks a period, not a timestamp.
    period_start: Mapped[Date] = mapped_column(Date, index=True)
    period_end: Mapped[Date] = mapped_column(Date, index=True)

    # The runner's own strings (Strava activity types, e.g. "Run"/"Ride"), for
    # display. Empty list means every discipline.
    disciplines: Mapped[list] = mapped_column(JSON, default=list)
    # The canonicalised identity form of `disciplines` — sorted, lower-cased,
    # comma-joined, "all" for empty. See `period_report_store.disciplines_key`,
    # the one function that computes it; this column is never written any other
    # way.
    disciplines_key: Mapped[str] = mapped_column(String, default="all")

    # "generating" | "ready" | "failed".
    status: Mapped[str] = mapped_column(String, default="generating", index=True)

    prompt_id: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)
    model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # The generated prose + a thin structured tail, the CoachMessageReport shape
    # reused (see `services/coach/period_report.py`). Null while generating.
    report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # The pack this report was generated from — audit/debug, never re-served raw.
    context_pack: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Provenance + failure detail (mirrors CoachReport.meta / TrainingPlan.failure_kind).
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", backref="period_reports")
