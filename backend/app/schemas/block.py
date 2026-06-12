"""Block correction API schemas (A1, ADR 0011)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BlockSplitRequest(BaseModel):
    """Split the block at this activity: it and every later member move to a
    new block."""

    activity_id: uuid.UUID


class BlockMergeRequest(BaseModel):
    """Merge the named adjacent block into the one in the URL."""

    other_block_id: uuid.UUID


class BlockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    start_date: datetime
    end_date: datetime
    primary_activity_id: uuid.UUID
    user_corrected: bool
