import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.coach_report import CoachReport
from app.schemas.chat import ChatHistoryResponse, ChatMessageSend
from app.schemas.coach import CoachReportRead
from app.services.coach.chat import get_chat_history, stream_chat_response
from app.services.coach.service import (
    get_active_report_row,
    get_or_generate_coach_report,
    _to_read,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse_data(text: str) -> str:
    """Frame one chunk as an SSE `data:` event.

    The payload is JSON-encoded so it survives the SSE line protocol intact: a
    coach reply is multi-paragraph markdown, and a raw newline inside `data: …`
    would split the value across lines (everything after the first newline is
    silently dropped by an SSE parser) or, on a blank line, dispatch a truncated
    event. JSON escaping collapses every chunk to a single line with no blank
    lines, so the client reconstructs the exact text. The `[DONE]` sentinel is
    sent unencoded and checked for before any JSON parse on the client.
    """
    return f"data: {json.dumps(text)}\n\n"


@router.get(
    "/activities/{activity_id}/coach-report",
    response_model=CoachReportRead,
)
async def get_coach_report(
    activity_id: UUID,
    generate: bool = Query(True, description="If false, only return cached report (404 if none)"),
    force: bool = Query(False, description="If true, regenerate the active-version report (prior versions retained)"),
    db: Session = Depends(get_db),
):
    if not generate and not force:
        existing = get_active_report_row(db, str(activity_id))
        if not existing:
            raise HTTPException(status_code=404, detail="No cached report.")
        return _to_read(existing)

    report = await get_or_generate_coach_report(db, str(activity_id), force=force)
    if not report:
        raise HTTPException(
            status_code=404,
            detail="Activity not found or metrics not yet computed.",
        )
    return report


@router.get(
    "/activities/{activity_id}/coach-chat",
    response_model=ChatHistoryResponse,
)
def get_chat(activity_id: UUID, db: Session = Depends(get_db)):
    """Return conversation history for an activity."""
    messages = get_chat_history(db, str(activity_id))
    return ChatHistoryResponse(messages=messages)


@router.delete("/activities/{activity_id}/coach-chat", status_code=204)
def delete_chat(activity_id: UUID, db: Session = Depends(get_db)):
    """Clear conversation history for an activity."""
    from app.models.coach_chat_message import CoachChatMessage

    db.query(CoachChatMessage).filter(
        CoachChatMessage.activity_id == activity_id
    ).delete()
    db.commit()


@router.post("/activities/{activity_id}/coach-chat")
async def post_chat(
    activity_id: UUID,
    body: ChatMessageSend,
    db: Session = Depends(get_db),
):
    """Send a message and stream the coach's response via SSE."""
    # Validate that a coach report exists
    existing = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity_id)
        .first()
    )
    if not existing:
        raise HTTPException(
            status_code=400,
            detail="Generate a coach report before starting a conversation.",
        )

    async def event_stream():
        # Flush a comment frame immediately so the connection (and its 200) is
        # established before the slow first token, rather than going silent
        # through proxies that would otherwise time the request out (#223).
        yield ": ok\n\n"
        try:
            async for chunk in stream_chat_response(db, activity_id, body.message):
                yield _sse_data(chunk)
        except Exception:
            # The stream is already open (status + headers sent), so a raised
            # exception here would just sever the connection — the browser
            # surfaces that as a bare "Load failed". Stream a readable message
            # instead so the user sees what happened and can retry (#223).
            logger.exception(
                "coach chat stream failed for activity %s", activity_id
            )
            yield _sse_data(
                "Sorry, I hit an error answering that. Please try again."
            )
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
