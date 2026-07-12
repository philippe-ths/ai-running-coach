from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChatMessageSend(BaseModel):
    message: str


class ChatMessageRead(BaseModel):
    id: UUID
    activity_id: UUID
    role: str
    content: str
    # #648 f/u: the on-demand data tools the coach ran for this turn (null when none),
    # so the UI can render a persistent "looked up …" trace after a reload.
    tools_used: Optional[List[str]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatHistoryResponse(BaseModel):
    messages: List[ChatMessageRead]
