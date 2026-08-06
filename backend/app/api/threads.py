"""Thread endpoints (#766, ADR 0027): the coach conversation from any screen.

List for the switcher, read one thread, rename, hard-delete, and the thread
turn itself (SSE). Every query is owner-scoped through the authenticated user;
a cross-user thread id resolves to 404, never to another runner's data.
"""

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, OwnedThread, require_owned_thread
from app.core.config import settings
from app.schemas.chat import ChatMessageRead
from app.schemas.thread import (
    ProposedActionConfirm,
    ThreadDetail,
    ThreadListResponse,
    ThreadMessageSend,
    ThreadRename,
)
from app.services import activity_queries
from app.services.coach import threads as thread_service

logger = logging.getLogger(__name__)


def require_threads_enabled() -> None:
    """#784: the thread surface's kill switch, applied to the whole router.

    Attached as a router-level dependency rather than per route, so a route added
    later cannot forget it — the switch has to hold for every way in, including
    the turn and the proposed-action confirm, or it is not a kill switch.
    """
    if not settings.COACH_THREADS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "The coach conversation is temporarily unavailable. Your runs "
                "still sync, your analysis still updates, and your reports still "
                "arrive after each session."
            ),
        )


router = APIRouter(
    prefix="/coach/threads", dependencies=[Depends(require_threads_enabled)]
)


@dataclass(frozen=True)
class ThreadTurn:
    """A validated thread turn plus the owned rows it addresses.

    The dependency carries the BODY as well as the resolved rows, and the
    handler takes only this. Declaring `ThreadMessageSend` in both the
    dependency and the handler would leave the OpenAPI schema intact (FastAPI
    counts body params by name) but validate the payload TWICE, so a malformed
    turn came back with every 422 entry duplicated.
    """

    body: ThreadMessageSend
    thread: Optional[object] = None
    anchor_activity: Optional[object] = None


def get_thread_turn(
    body: ThreadMessageSend, db: DbSession, user: CurrentUser
) -> ThreadTurn:
    """Resolve the thread turn's owned targets, both carried in the BODY.

    Order is load-bearing and preserved from the pre-#802 handler: a supplied
    `thread_id` is resolved first, and the anchor is only consulted when no
    thread was named. So a turn that continues an existing thread never
    validates (and never 404s on) an `anchor_activity_id` it would ignore.
    """
    thread = None
    if body.thread_id is not None:
        thread = require_owned_thread(db, body.thread_id, user)

    anchor_activity = None
    if thread is None and body.anchor_activity_id is not None:
        anchor_activity = activity_queries.get_owned_activity(
            db, body.anchor_activity_id, user.id
        )
        if anchor_activity is None:
            raise HTTPException(status_code=404, detail="Activity not found")

    return ThreadTurn(body=body, thread=thread, anchor_activity=anchor_activity)


ResolvedThreadTurn = Annotated[ThreadTurn, Depends(get_thread_turn)]


@router.get("", response_model=ThreadListResponse)
def list_threads(
    db: DbSession,
    user: CurrentUser,
):
    return ThreadListResponse(threads=thread_service.list_threads(db, user.id))


@router.post("/messages")
async def post_thread_message(
    turn: ResolvedThreadTurn,
    db: DbSession,
    user: CurrentUser,
):
    """Send a thread turn and stream the coach's reply via SSE.

    `thread_id` continues an existing thread; absent, a new thread is created
    (optionally anchored to an owned activity) and announced as the stream's
    first object frame. Ownership is denied BEFORE the stream opens.
    """
    body = turn.body
    thread = turn.thread
    anchor_activity = turn.anchor_activity

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
                screen=body.screen,
            ):
                if event.is_heartbeat:
                    yield ": hb\n\n"
                elif event.thread_meta is not None:
                    yield f"data: {json.dumps({'type': 'thread', **event.thread_meta})}\n\n"
                elif event.proposed_action is not None:
                    yield f"data: {json.dumps({'type': 'proposed_action', **event.proposed_action})}\n\n"
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
    thread: OwnedThread,
    db: DbSession,
):
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
    body: ThreadRename,
    thread: OwnedThread,
    db: DbSession,
):
    thread.title = body.title.strip()
    db.add(thread)
    db.commit()


@router.delete("/{thread_id}", status_code=204)
def delete_thread(
    thread: OwnedThread,
    db: DbSession,
):
    thread_service.delete_thread(db, thread)


@router.post("/actions/confirm")
def confirm_proposed_action(
    body: ProposedActionConfirm,
    db: DbSession,
    user: CurrentUser,
):
    from app.services.coach.proposed_actions import consume_and_execute

    try:
        result = consume_and_execute(db, user.id, body.token)
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposed action not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result
