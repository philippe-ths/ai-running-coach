from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime
from typing import Optional
from uuid import UUID


class UserCreate(BaseModel):
    email: Optional[EmailStr] = None


class UserRead(BaseModel):
    id: UUID
    email: Optional[EmailStr] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
