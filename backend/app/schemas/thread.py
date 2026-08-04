"""Thread read/write schemas (#766, ADR 0027).

The switcher list, the single-thread read, rename, and the thread-turn send.
A thread's display title resolves server-side: the written/runner title when
set, else the first user message trimmed — an untitled thread needs a name the
moment it has one turn in it.
"""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import ChatMessageRead

# The switcher folds threads idle beyond this many days under "Earlier"
# client-side; the server just orders by recency.
TITLE_MAX_LENGTH = 200
ASKED_FROM_MAX_LENGTH = 64


class ThreadAnchor(BaseModel):
    """The anchored activity, for the switcher chip and the report-at-head
    projection (the report itself is fetched through the existing coach-report
    endpoint — a read-time projection, never a copy)."""

    activity_id: UUID
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    distance_m: Optional[float] = None
    type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ThreadListItem(BaseModel):
    id: UUID
    title: str
    snippet: Optional[str] = None
    last_message_at: Optional[datetime] = None
    anchor: Optional[ThreadAnchor] = None


class ThreadListResponse(BaseModel):
    threads: List[ThreadListItem]


class ThreadDetail(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    last_message_at: Optional[datetime] = None
    anchor: Optional[ThreadAnchor] = None
    messages: List[ChatMessageRead]


class ThreadRename(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)


class ScreenPointer(BaseModel):
    """Which screen the runner is on and their view selections (#767, ADR 0028).

    A POINTER, not a payload: selections are the runner's input and are trusted
    as such; every number is recomputed server-side by the same builders that
    produced the screen. Deliberately no free-form or numeric fields — a fact
    can never travel from the client through this shape.
    """

    screen: Literal["home", "activities", "activity", "load", "trends", "profile"]
    # activity screens: which run is on screen (owner-verified at resolution).
    activity_id: Optional[UUID] = None
    # trends screens: the selected range and activity-type filter.
    range: Optional[Literal["7D", "30D", "3M", "6M", "1Y"]] = None
    types: Optional[List[str]] = Field(default=None, max_length=10)

    model_config = ConfigDict(extra="forbid")


class ThreadMessageSend(BaseModel):
    message: str = Field(min_length=1)
    # Continue this thread; absent = start a new one with this message.
    thread_id: Optional[UUID] = None
    # Anchor for a NEW thread only (ignored when thread_id is given): the
    # activity whose page the thread was born on. A framing hint, never a data
    # boundary (ADR 0027).
    anchor_activity_id: Optional[UUID] = None
    # The screen pointer this turn was asked from (#767): resolved server-side
    # into the "looking at" view. Optional — a turn without one simply carries
    # no screen context.
    screen: Optional[ScreenPointer] = None
    # Fallback provenance label for clients not yet sending a pointer; when
    # `screen` is present the label derives from it and this is ignored.
    asked_from: Optional[str] = Field(default=None, max_length=ASKED_FROM_MAX_LENGTH)


class ProposedActionConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=64)
