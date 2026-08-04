"""Thread read/write schemas (#766, ADR 0027).

The switcher list, the single-thread read, rename, and the thread-turn send.
A thread's display title resolves server-side: the written/runner title when
set, else the first user message trimmed — an untitled thread needs a name the
moment it has one turn in it.
"""

from datetime import datetime
from typing import List, Optional
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


class ThreadMessageSend(BaseModel):
    message: str = Field(min_length=1)
    # Continue this thread; absent = start a new one with this message.
    thread_id: Optional[UUID] = None
    # Anchor for a NEW thread only (ignored when thread_id is given): the
    # activity whose page the thread was born on. A framing hint, never a data
    # boundary (ADR 0027).
    anchor_activity_id: Optional[UUID] = None
    # The label of the screen the turn was asked from (provenance only; the
    # resolved screen view is slice 3, ADR 0028).
    asked_from: Optional[str] = Field(default=None, max_length=ASKED_FROM_MAX_LENGTH)
