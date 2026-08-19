"""One training plan: the coach's standing answer to "what are we doing next" (#830).

A plan is the container for three things that are always read together and are
always rewritten together when the coach revises: the horizon's week SHAPE, the
spacing RULES the week must satisfy, and the pointer to the race it is built for.
The concrete sessions are their own table (`planned_sessions`) because they are
queried by date, joined to activities on completion, and updated one at a time.

Why `rules` and `week_shapes` are JSON rather than tables
---------------------------------------------------------
Both are small, bounded, always fetched whole, never queried independently, and
replaced wholesale on every revision — the same shape as `RunnerMemory`'s profile
and `CoachingRelationship.receipt_templates`, both of which are JSON here for the
same reasons. "Rules are first-class data, not prose in a chat message" (#830)
stays true: they are strict-coerced through `schemas/schedule.py` on the way in
and out, and their kinds come from a closed code-resident vocabulary
(`services/schedule/rules.py`) with a pure predicate each, so a rule violation is
DETECTABLE rather than merely readable. A rule the coach invents is rejected at
coercion; it cannot reach the store.

`status` is "active" | "superseded". At most one active plan per user, but the
constraint is held by the writer rather than the schema, because superseding is
a two-row transition (deactivate, insert) and a partial unique index is Postgres
syntax the SQLite test database cannot see — a constraint the tests could not
exercise would be a constraint in name only.

"No plan" is the absence of an active plan, not a plan with a flag. Free mode
(requirement 8) reads the same week endpoint and gets actuals and the runner's
own norm; nothing about it is a second product.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # "active" | "superseded"
    status: Mapped[str] = mapped_column(String, default="active", index=True)

    goal_race_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("goal_races.id"), nullable=True
    )

    # The last week the plan says anything about at all, concrete or sketched.
    horizon_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # List[SpacingRule] — see schemas/schedule.py.
    rules: Mapped[list] = mapped_column(JSON, default=list)
    # List[PlannedWeekShape] — the horizon's per-week load/mix/phase. Weeks that
    # also have `planned_sessions` rows read as PLANNED; weeks with only a shape
    # read as SKETCHED. That distinction is derived, never stored, so it cannot
    # drift away from what is actually there.
    week_shapes: Mapped[list] = mapped_column(JSON, default=list)

    # Provenance. "coach" for a generated plan, "runner" for one the runner's own
    # confirmed edits produced. `model_id` is the model the draft actually ran on.
    source: Mapped[str] = mapped_column(String, default="coach")
    model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When this plan STOPPED being the runner's plan; null while it is active or
    # has never been active (#857). Written only by `store.activate_plan`, on the
    # rows it supersedes, and cleared on the row it activates.
    #
    # It exists because "the plan I was training to before this one" is a
    # question no other column answers. `created_at` orders plans by when they
    # were WRITTEN, and a restore makes an older row current again, so after two
    # swaps the newest superseded row is no longer the one just stepped away
    # from. `generated_at` orders them by when they were last made active, which
    # works only if a restore re-stamps it, and re-stamping would destroy the one
    # field saying how old the plan's thinking is, on the feature whose whole
    # point is that stepping between plans destroys nothing. So the transition
    # gets its own recorded fact rather than being inferred from a proxy.
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", backref="training_plans")
    goal_race = relationship("GoalRace")
    sessions = relationship(
        "PlannedSession", back_populates="plan", cascade="all, delete-orphan"
    )
