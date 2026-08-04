from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class ChatMessageSend(BaseModel):
    message: str


class ToolTraceEntry(BaseModel):
    """One tool call's compact trace of WHAT it fetched (#664): the tool name, a
    past-tense friendly label, the resolved window, and a result count. Rendered as
    one chip so the runner can sanity-check the data the coach reasoned over. Every
    field is server-derived; no model prose enters the trace."""

    tool: str
    label: Optional[str] = None
    detail: Optional[str] = None
    count: Optional[int] = None


class ChatMessageRead(BaseModel):
    id: UUID
    # #765: null for a thread-only turn (a thread need not be anchored to an
    # activity); the activity chat box's rows always carry it.
    activity_id: Optional[UUID] = None
    role: str
    content: str
    # #766: the label of the screen this turn was asked from (ADR 0028: past
    # turns retain the label only), rendered as the quiet "asked from …" note.
    asked_from: Optional[str] = None
    # #648 f/u / #664: the on-demand data tools the coach ran for this turn (null when
    # none), as one record per call describing what each fetched, so the UI can render
    # a persistent "looked up …" trace after a reload.
    tools_used: Optional[List[ToolTraceEntry]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("tools_used", mode="before")
    @classmethod
    def _coerce_tools_used(cls, value: Any) -> Any:
        """Normalise the stored `tools_used` JSON into `ToolTraceEntry` records.

        Pre-#664 rows stored a bare list of tool-name strings; coerce each into a
        record (filling the friendly label from the server-owned verb map) so a
        reloaded UI renders legacy turns uniformly with no client-side legacy branch.
        New rows already store the record dicts and pass through unchanged."""
        if not value:
            return value
        from app.services.coach.query_tools import trace_label

        coerced = []
        for item in value:
            if isinstance(item, str):
                coerced.append({"tool": item, "label": trace_label(item)})
            else:
                coerced.append(item)
        return coerced


class ChatHistoryResponse(BaseModel):
    messages: List[ChatMessageRead]
