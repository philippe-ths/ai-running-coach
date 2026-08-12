"""One planned session: the unit the schedule is made of (#830).

Three independent axes, exactly as the design settled them, and keeping them
independent is what lets ONE screen serve a rigid weekday plan, a loose
four-session week, and no plan at all:

1. PLACEMENT — where in the week it sits.
2. COMMITMENT — `committed` (part of the plan) or `suggested` (the coach thinks
   it would serve you; ignoring it leaves no trace and generates no follow-up).
3. DISCIPLINE — run, walk, bike, strength, row. Orthogonal to `intent`: an easy
   bike and an easy run are the same stimulus, so intent carries the reading and
   discipline is named alongside it.

Placement is a WINDOW, and pinning is derived
---------------------------------------------
There is no `placement` column and no mode flag. Every session stores an
inclusive `[window_start, window_end]`, and the three placements the design names
fall out of its width:

    window_start == window_end            -> pinned to that day
    the window spans the whole week       -> floating anywhere in the week
    anything between                      -> floating in a window

A stored mode would be a second copy of a fact the dates already carry, and the
two could disagree. Deriving it also buys the behaviour the design asks for
elsewhere for free: as days pass, a floating session's EFFECTIVE window is
`max(window_start, today) .. window_end`, computed at read time. The stored
window never moves, "narrowed from Thu-Sat" is stored-vs-effective, and no job
runs per session to make it true. When the effective window empties, the session
is missed — also derived, also not a flag.

A window lies within one week (validated at the API and by the plan validator),
so the week a session belongs to is `week_start(window_start)` and is likewise
not stored. That is the one-fact-one-place discipline this codebase holds
elsewhere (`activity_facts`, `weeks`), applied here.

`structure` is the shape `workout_matching.match_planned_to_detected` has been
waiting for since the beginning: `{"reps_planned": 8, "rep_distance_m": 400,
"rest_s": 60}`. `_extract_planned_workout` has returned None as a placeholder all
along, so that comparison has never had a plan to make. Wiring it is #830's
completion slice, not this one; the column lands now so the feature needs one
migration rather than two.

The completion columns are likewise present-but-unwritten here for the same
reason. All three ways to finish a session — auto-matched from a synced activity,
tapped on the card, or told to the coach in conversation — converge on these same
columns through one writer, the way the in-app form and the Telegram tap both go
through `write_checkin`.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class PlannedSession(Base):
    __tablename__ = "planned_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=generate_uuid
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_plans.id"), index=True
    )
    # Denormalised owner, deliberately: every read of this table is owner-scoped
    # (ADR 0022), and routing each one through a join to `training_plans` would
    # make tenant scoping depend on remembering the join. The FK is the scope.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # Placement. Inclusive, both ends, always within one week.
    window_start: Mapped[date] = mapped_column(Date, index=True)
    window_end: Mapped[date] = mapped_column(Date)

    # "rest" | "easy" | "long" | "quality" | "strength" — the reading.
    intent: Mapped[str] = mapped_column(String)
    # "run" | "walk" | "bike" | "strength" | "row" | "other" — the modality.
    discipline: Mapped[str] = mapped_column(String)
    # "committed" | "suggested"
    commitment: Mapped[str] = mapped_column(String, default="committed")

    title: Mapped[str] = mapped_column(String(120))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    target_distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_duration_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The load this session is worth, in the one unit that sums across a gym
    # session and a turbo ride (`effort_score`). It is what sizes the discipline
    # mix bar and the horizon's bar length; km cannot, because strength and an
    # indoor bike have no distance.
    target_effort_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # {"reps_planned": int, "rep_distance_m": float, "rest_s": float} or None.
    structure: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # --- completion (written by the completion slice, not by this one) ---
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_activity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("activities.id"), nullable=True
    )
    # "auto" | "manual" | "conversation"
    completion_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # A dismissed SUGGESTION. Never set on a committed session — declining
    # something you agreed to is a plan change, and those go through the coach.
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    plan = relationship("TrainingPlan", back_populates="sessions")
    completed_activity = relationship("Activity")
