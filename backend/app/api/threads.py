"""Thread endpoints (#766, ADR 0027): the coach conversation from any screen.

List for the switcher, read one thread, rename, hard-delete, and the thread
turn itself (SSE). Every query is owner-scoped through the authenticated user;
a cross-user thread id resolves to 404, never to another runner's data.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatMessageRead
from app.schemas.thread import (
    ThreadDetail,
    ThreadListResponse,
    ThreadMessageSend,
    ThreadRename,
)
from app.services import activity_queries
from app.services.coach import threads as thread_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coach/threads")


def _owned_thread_or_404(db: Session, thread_id: UUID, user: User):
    thread = thread_service.get_owned_thread(db, thread_id, user.id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


@router.get("", response_model=ThreadListResponse)
def list_threads(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    return ThreadListResponse(threads=thread_service.list_threads(db, user.id))


@router.post("/messages")
async def post_thread_message(
    body: ThreadMessageSend,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Send a thread turn and stream the coach's reply via SSE.

    `thread_id` continues an existing thread; absent, a new thread is created
    (optionally anchored to an owned activity) and announced as the stream's
    first object frame. Ownership is denied BEFORE the stream opens.
    """
    thread = None
    if body.thread_id is not None:
        thread = _owned_thread_or_404(db, body.thread_id, user)

    anchor_activity = None
    if thread is None and body.anchor_activity_id is not None:
        anchor_activity = activity_queries.get_owned_activity(
            db, body.anchor_activity_id, user.id
        )
        if anchor_activity is None:
            raise HTTPException(status_code=404, detail="Activity not found")

    from app.services.coach.thread_turn import stream_thread_turn

    async def event_stream():
        # Establish the connection (and its 200) before the slow first token
        # (#223); heartbeats keep it alive through buffering (#375).
        yield ": ok\n\n"
        try:
            async for event in stream_thread_turn(
                db,
                user,
                message=body.message,
                thread=thread,
                anchor_activity=anchor_activity,
                asked_from=body.asked_from,
            ):
                if event.is_heartbeat:
                    yield ": hb\n\n"
                elif event.thread_meta is not None:
                    yield f"data: {json.dumps({'type': 'thread', **event.thread_meta})}\n\n"
                elif event.status_label:
                    yield f"data: {json.dumps({'type': 'status', 'label': event.status_label, 'tool': event.status_tool})}\n\n"
                elif event.trace_entry is not None:
                    yield f"data: {json.dumps({'type': 'tool_trace', 'entry': event.trace_entry})}\n\n"
                else:
                    yield f"data: {json.dumps(event.text)}\n\n"
        except Exception:
            logger.exception("thread turn stream failed (thread %s)", body.thread_id)
            yield f"data: {json.dumps('Sorry, I hit an error answering that. Please try again.')}\n\n"
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


@router.get("/{thread_id}", response_model=ThreadDetail)
def get_thread(
    thread_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    thread = _owned_thread_or_404(db, thread_id, user)
    messages = thread_service.thread_messages(db, thread)
    return ThreadDetail(
        id=thread.id,
        title=thread_service.display_title(db, thread),
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        anchor=thread_service._anchor_for(thread),
        messages=[ChatMessageRead.model_validate(m) for m in messages],
    )


@router.patch("/{thread_id}", status_code=204)
def rename_thread(
    thread_id: UUID,
    body: ThreadRename,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    thread = _owned_thread_or_404(db, thread_id, user)
    thread.title = body.title.strip()
    db.add(thread)
    db.commit()


@router.delete("/{thread_id}", status_code=204)
def delete_thread(
    thread_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    thread = _owned_thread_or_404(db, thread_id, user)
    thread_service.delete_thread(db, thread)
