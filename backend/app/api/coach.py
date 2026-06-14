import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.profile import get_current_user_profile
from app.db.session import get_db
from app.models import Activity, CoachingRelationship
from app.models.coach_report import CoachReport
from app.schemas.chat import ChatHistoryResponse, ChatMessageSend
from app.schemas.coach import CoachReportRead
from app.schemas.voice import VoiceConfigRead, VoiceDials, build_catalog
from app.services.coach.chat import get_chat_history, stream_chat_response
from app.services.coach.service import (
    get_displayable_report_row,
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


def _get_or_create_relationship(db: Session) -> CoachingRelationship:
    """Resolve the current runner's coaching_relationship row, creating it if needed.

    Reuses the profile helper's user resolution (Strava-linked user first), which
    also auto-creates the thin relationship row the way it auto-creates the default
    profile, so a runner who never edited their profile still has a row to read/write.
    """
    profile = get_current_user_profile(db)
    return (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == profile.user_id)
        .first()
    )


def _read_voice(relationship: CoachingRelationship) -> VoiceConfigRead:
    current = VoiceDials(
        preset=relationship.voice_preset,
        warmth=relationship.voice_warmth,
        humor=relationship.voice_humor,
        directness=relationship.voice_directness,
        energy=relationship.voice_energy,
        freetext=relationship.voice_freetext,
    )
    return VoiceConfigRead(current=current, catalog=build_catalog())


@router.get("/coach/voice", response_model=VoiceConfigRead)
def get_voice(db: Session = Depends(get_db)):
    """The runner's declared coach voice plus the catalog the UI renders from."""
    return _read_voice(_get_or_create_relationship(db))


@router.put("/coach/voice", response_model=VoiceConfigRead)
def update_voice(voice_in: VoiceDials, db: Session = Depends(get_db)):
    """Set the runner's declared coach voice (preset + four dials + free-text).

    Runner-sovereign (ADR 0012): this is the only writer of voice state — no
    background job ever changes it. Voice flexes delivery only (ADR 0013); the
    free-text is stored verbatim and framed as untrusted tone-data at prompt time,
    never as instructions.
    """
    relationship = _get_or_create_relationship(db)
    relationship.voice_preset = voice_in.preset
    relationship.voice_warmth = voice_in.warmth
    relationship.voice_humor = voice_in.humor
    relationship.voice_directness = voice_in.directness
    relationship.voice_energy = voice_in.energy
    relationship.voice_freetext = voice_in.freetext
    db.add(relationship)
    db.commit()
    db.refresh(relationship)
    return _read_voice(relationship)


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
    # Display-safe (#261): unless an explicit regenerate (force) was asked for,
    # prefer ANY cached report over a synchronous regeneration. A COACH_PROMPT_ID
    # flip (e.g. activating voice coach_message_v3) leaves prior-version reports
    # displayable rather than forcing a regen that exceeds the gateway timeout
    # (#260). Only fall through to generation when no report exists at all.
    if not force:
        existing = get_displayable_report_row(db, str(activity_id))
        if existing:
            return _to_read(existing)
        if not generate:
            raise HTTPException(status_code=404, detail="No cached report.")

    report = await get_or_generate_coach_report(db, str(activity_id), force=force)
    if not report:
        raise HTTPException(
            status_code=404,
            detail="Activity not found or metrics not yet computed.",
        )
    return report


@router.post(
    "/activities/{activity_id}/coach-report/regenerate",
    status_code=202,
)
def regenerate_coach_report(activity_id: UUID, db: Session = Depends(get_db)):
    """Kick off an asynchronous coach-report regeneration (#260).

    The two-stage generation is a 30-120s LLM call — far past the gateway timeout —
    so the synchronous force path 504s. This enqueues the work onto the worker
    (where new runs already generate) and returns immediately; the frontend polls
    the GET endpoint for the fresh report. Returns 404 when the activity has no
    metrics to regenerate from.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity or not activity.metrics:
        raise HTTPException(
            status_code=404,
            detail="Activity not found or metrics not yet computed.",
        )
    # Imported here to keep the queue/job dependency off the module import path of
    # the read endpoints (and out of test collection that does not touch Redis).
    from app.core.config import settings
    from app.core.queue import queue
    from app.jobs.process_new_activity import regenerate_report_job

    # job_timeout (#264): the two-stage regeneration runs ~120-360s, past RQ's 180s
    # default; without this the worker's death penalty kills it before it stores the
    # report. (The queue default_timeout also covers it; explicit here documents it.)
    queue.enqueue(
        regenerate_report_job, str(activity_id),
        job_timeout=settings.RQ_JOB_TIMEOUT_SECONDS,
    )
    return {"status": "regenerating"}


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
