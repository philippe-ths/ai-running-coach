"""The runner's stated goal race (#830).

`UserProfile.upcoming_races` has held races as an untyped JSON blob since the
beginning, and nothing in the backend has ever read it — not the coach pack, not
the thread turn, not one service. A plan cannot be anchored to a blob. The
horizon's phases, its peak week and its taper are all measured BACKWARDS from a
date, and the shape of the block is chosen from a distance, so those two facts
have to be typed and queryable rather than whatever the frontend last wrote.

Deliberately NOT removed here: `upcoming_races` itself. Deleting it is its own
dependency sweep (the frontend profile type still declares it, and the profile
form still round-trips it), and the schedule does not need it gone to be correct.
Recorded as follow-up rather than done quietly.

`priority` is the runner's own ranking (A = the race the block is built for,
B/C = races run through). It steers how hard the plan bends around the date; it
is never a claim about the runner's ability.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class GoalRace(Base):
    __tablename__ = "goal_races"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=generate_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    name: Mapped[str] = mapped_column(String(200))
    # A goal is held as precisely as the runner holds it (#1042): an exact
    # `race_date`, or a `window_start`..`window_end` ("~March", "May to June"),
    # or neither ("someday"). Never both; the API enforces it.
    race_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    window_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    window_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Metres, like every other distance in this codebase (Activity.distance,
    # DerivedMetric, the fact stream). The UI speaks km; the store speaks metres.
    # Null for a goal with no fixed distance (a volume block, a backyard ultra).
    distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_time_s: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The runner's own words for the coach. Reaches prompts quoted as theirs.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    booked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # "A" | "B" | "C" — plain String like every other enum-ish column here; the
    # allowed set is validated at the API (the `UserMaterial.kind` precedent).
    priority: Mapped[str] = mapped_column(String, default="A")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", backref="goal_races")
