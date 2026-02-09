import uuid

from sqlalchemy import String, ForeignKey, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import generate_uuid


class ActivityStream(Base):
    __tablename__ = "activity_streams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"))
    stream_type: Mapped[str] = mapped_column(String)  # time, distance, watts, heartrate
    data: Mapped[list] = mapped_column(JSON)

    activity = relationship("Activity", back_populates="streams")
