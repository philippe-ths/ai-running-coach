from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.coach_report import CoachReport
from app.schemas.chat import ChatHistoryResponse, ChatMessageSend
from app.schemas.coach import CoachReportRead
from app.services.coach.chat import get_chat_history, stream_chat_response
from app.services.coach.service import get_or_generate_coach_report, _to_read

router = APIRouter()


@router.get(
    "/activities/{activity_id}/coach-report",
    response_model=CoachReportRead,
)
async def get_coach_report(
    activity_id: UUID,
    generate: bool = Query(True, description="If false, only return cached report (404 if none)"),
    force: bool = Query(False, description="If true, delete cached report and regenerate"),
    db: Session = Depends(get_db),
):
    if force:
        existing = (
            db.query(CoachReport)
            .filter(CoachReport.activity_id == str(activity_id))
            .first()
        )
        if existing:
            db.delete(existing)
            db.commit()

    if not generate and not force:
        existing = (
            db.query(CoachReport)
            .filter(CoachReport.activity_id == str(activity_id))
            .first()
        )
        if not existing:
            raise HTTPException(status_code=404, detail="No cached report.")
        return _to_read(existing)

    report = await get_or_generate_coach_report(db, str(activity_id))
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
        .filter(CoachReport.activity_id == str(activity_id))
        .first()
    )
    if not existing:
        raise HTTPException(
            status_code=400,
            detail="Generate a coach report before starting a conversation.",
        )

    async def event_stream():
        async for chunk in stream_chat_response(db, str(activity_id), body.message):
            # SSE format: data lines followed by blank line
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
