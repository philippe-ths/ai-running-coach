"""The season: the coach's read of every goal and the timeline it plans to (#1064).

A season sits ABOVE the training plan. It holds the opinion (what each goal is,
when to do it, which events to enter, how the months between are spent) and the
plan's weeks are written under it. The two are separate rows because they change
at different rates: a season is rewritten when the runner's goals change, a plan
when the runner's weeks do.

`plan` is JSON for the reasons `TrainingPlan.week_shapes` is: small, always
fetched whole, replaced wholesale, and strict-coerced through
`schemas/season.py` on the way out, so an off-shape row fails at the boundary
rather than reaching a screen or a prompt.

`status` is "drafting" | "active" | "superseded" | "failed". At most one active
season per user, held by the writer rather than the schema for the reason
`training_plans` states: a partial unique index is Postgres syntax the SQLite
test database cannot exercise.

`goals_fingerprint` is a hash of the upcoming goals the season was written
against, so "the runner's goals have changed since" is a comparison rather than
a guess.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # "drafting" | "active" | "superseded" | "failed"
    status: Mapped[str] = mapped_column(String, default="drafting", index=True)

    # `SeasonPlan` as JSON; null until a generation passes its checks.
    plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    goals_fingerprint: Mapped[str] = mapped_column(String, default="")
    # `DraftLog` as JSON: attempts, failures, tokens, cost. Kept on a failed
    # season too, because a failure is exactly when the numbers are wanted.
    draft_log: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Runner-facing sentence for a failed season; the checks' own text is written
    # to be fed back into a prompt and stays in `draft_log`.
    failure_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", backref="seasons")
