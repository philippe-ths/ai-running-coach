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

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Uuid, func
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
    race_date: Mapped[date] = mapped_column(Date, index=True)
    # Metres, like every other distance in this codebase (Activity.distance,
    # DerivedMetric, the fact stream). The UI speaks km; the store speaks metres.
    distance_m: Mapped[float] = mapped_column(Float)
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
