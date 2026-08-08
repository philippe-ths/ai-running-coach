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

from sqlalchemy import DateTime, ForeignKey, JSON, SmallInteger, String, Uuid
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
    # `voice_force` was `voice_directness` (#822): the axis measures IMPACT, not
    # clarity -- a gentle coach can be perfectly clear, and a brutal one is more
    # than merely blunt. Stored values carry over unchanged; only the name moved.
    voice_force: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    voice_energy: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    # #822: nothing else controlled how much room the coach takes, and length
    # changes the read more than any tonal axis.
    voice_length: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
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

    # The #296 receipt cadence: pre-generated voiced receipt phrasings (one set of
    # slotted variants per block-aware situation), produced OFFLINE through the
    # runner's declared Voice (a small LLM job on PUT /api/coach/voice, lazily if
    # missing) and validated so voice flexes delivery only — never the deterministic
    # block facts or the safety floor (ADR 0013). Served deterministically at receipt
    # time by filling the block facts into a picked variant. Null until generated;
    # the house-default deterministic set is the FLOOR when null or generation fails.
    # `receipt_templates_voice_key` is the fingerprint of the voice inputs the stored
    # set was generated from, so a voice change is detectable and triggers regen.
    receipt_templates: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    receipt_templates_voice_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    receipt_templates_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user = relationship("User", backref="coaching_relationship")
