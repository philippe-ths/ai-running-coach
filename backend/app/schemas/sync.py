from typing import List

from pydantic import BaseModel


class SyncResponse(BaseModel):
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    analyzed: int = 0
    errors: List[str] = []
