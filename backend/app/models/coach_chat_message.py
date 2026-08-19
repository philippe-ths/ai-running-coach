import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, DateTime, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachChatMessage(Base):
    __tablename__ = "coach_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    # #765: messages belong to a Thread (ADR 0027). Nullable in the schema so a
    # row written by pre-thread code during a deploy window never violates the
    # constraint; the write path always sets it and the activity-thread read path
    # adopts any orphan, so the data converges to every-message-has-a-thread.
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("coach_threads.id"), nullable=True, index=True
    )
    # #765: nullable since a thread turn need not be anchored to an activity;
    # the activity chat box still populates it (dual-write) so the existing
    # activity-scoped readers keep working unchanged.
    activity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("activities.id"), nullable=True, index=True
    )
    # "user" or "assistant" — what was said — plus "event" (#778), the app's own
    # record of a proposed action the runner confirmed in this thread. An event
    # row is neither side speaking: it never becomes a turn sent to the model, is
    # never read as a runner statement or a coach claim, and reaches the coach
    # only through the system prompt's ledger, where it is labelled for what it
    # is. `services/coach/threads.CONVERSATIONAL_ROLES` is the filter.
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # #648 follow-up / #664: the on-demand data tools the coach ran to produce this
    # assistant turn, one record per call ({tool, label, detail, count}) describing
    # WHAT each fetched (resolved window + result count), so the UI can show a
    # persistent "looked up …" trace that survives a reload. Null on user turns and on
    # assistant turns that answered without a fetch. Pre-#664 rows hold a bare list of
    # tool-name strings; the read schema coerces those into records on load, so no
    # data migration is needed for this generic JSON column.
    tools_used: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # #765 per-turn provenance (cannot be reconstructed later, so it ships now):
    # the label of the screen this turn was asked from (ADR 0028: past turns
    # retain the label only, never the resolved view), and the coaching skills
    # loaded for the turn (ADR 0029; null until slice 5 populates it).
    asked_from: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    skills_used: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    activity = relationship("Activity", backref="chat_messages")
    thread = relationship("Thread", backref="messages")
