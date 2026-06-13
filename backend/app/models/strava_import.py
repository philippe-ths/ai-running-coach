import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.base import generate_uuid


class StravaImport(Base):
    """State for a resumable historical Strava import (#239).

    A self-pacing background job walks the runner's Strava history from
    ``since_date`` to today, ingesting activity summaries (no streams, no AI
    coach analysis, no notifications). State lives entirely in this row so the
    job is resumable: ``cursor_page`` is the next page to fetch (with a fixed
    ``after`` lower bound), advancing one page per full batch, so an interruption
    resumes without re-walking completed pages, and dedup by
    ``strava_activity_id`` makes any overlap idempotent.
    """

    __tablename__ = "strava_imports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

    # Lower bound the runner chose: import activities on or after this date.
    since_date: Mapped[date] = mapped_column(Date)

    # "running" | "completed" | "failed".
    status: Mapped[str] = mapped_column(String, default="running")

    # Next page to fetch (1-based), with a fixed `after` lower bound. Strava
    # returns oldest-first when `after` is set, so page-number pagination walks
    # the whole window; the cursor advances by one per full page.
    cursor_page: Mapped[int] = mapped_column(Integer, default=1)

    # How many activities have been upserted so far (progress for the UI).
    activities_imported: Mapped[int] = mapped_column(Integer, default=0)

    # Last error if the import failed; NULL otherwise.
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )
