"""
CoachingRelationship — the thin durable anchor of the coaching relationship
(A1, ADR 0011, owner-ratified fork). One row per user, deliberately minimal:
P1's voice/stance dials and later relationship state ALTER this table rather
than create it. Auto-created alongside the user on first profile read, the
way `UserProfile` is.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class CoachingRelationship(Base):
    __tablename__ = "coaching_relationship"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # P1.1 Voice dials (ADR 0012/0013). All nullable: a null voice resolves to the
    # moderate default at read time, so the migration backfills nothing and pre-voice
    # behaviour is unchanged until a runner declares a voice. `voice_preset` is stored
    # (not just the dial numbers) so the prompt can inject the preset's example
    # messages — the highest-leverage steering ingredient. The four dials are 1-5 on
    # the operable axes (Clinical-Warm, Earnest-Playful, Gentle-Blunt, Calm-Fired-up);
    # `voice_freetext` is the untrusted tone-data escape-hatch (never instructions).
    voice_preset: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    voice_warmth: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    voice_humor: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    voice_directness: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    voice_energy: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    voice_freetext: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # P1.3 Coaching stance (ADR 0015). All nullable: a null stance resolves to the
    # default school (aerobic-base) + balanced emphasis at read time, so the
    # migration backfills nothing and pre-stance behaviour is unchanged until a
    # runner declares a stance. `stance_school` is a corpus.SCHOOLS key (the school
    # the coach reasons from). The two emphasis axes are 1-5: Data 1 - Sentiment 5,
    # Process 1 - Outcome 5. Runner-sovereign like voice — written only by
    # PUT /api/coach/stance, never inferred by a background job.
    stance_school: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stance_data_sentiment: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    stance_process_outcome: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    user = relationship("User", backref="coaching_relationship")
