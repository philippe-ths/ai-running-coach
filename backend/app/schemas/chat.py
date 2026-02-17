from datetime import datetime
from typing import List
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChatMessageSend(BaseModel):
    message: str


class ChatMessageRead(BaseModel):
    id: UUID
    activity_id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatHistoryResponse(BaseModel):
    messages: List[ChatMessageRead]
