import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.db.session import get_db
from app.models import Activity, StravaAccount
from app.core.queue import queue
from app.jobs.process_new_activity import (
    PIPELINE_RETRY,
    mark_done_and_schedule,
    process_new_activity_job,
)
from app.jobs.strava_sync import sync_activity_job
from app.schemas import CheckInCreate
from app.services.blocks import remove_activity_from_block
from app.services.checkins import write_checkin
from app.services.notifications import get_notifier
from app.services.notifications.callback_token import decode as decode_callback_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Schema for the incoming webhook event
# https://developers.strava.com/docs/webhooks/
class StravaEvent(BaseModel):
    object_type: str  # activity, athlete
    object_id: int    # activity ID or athlete ID
    aspect_type: str  # create, update, delete
    owner_id: int     # athlete ID
    subscription_id: int
    updates: dict = {} # e.g. title changes
    event_time: int

@router.get("/webhooks/strava")
def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    verify_token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge")
):
    """
    Strava verification challenge.
    """
    expected_token = settings.STRAVA_WEBHOOK_VERIFY_TOKEN
    if not expected_token and settings.APP_ENV == "production":
        # Fail closed: an empty configured token would otherwise let anyone
        # register a webhook subscription against this deployment by sending
        # ?hub.verify_token= against the default of "".
        logger.critical(
            "strava_webhook_verify_token_missing_in_production",
        )
        raise HTTPException(
            status_code=503,
            detail="Service unavailable: webhook verify token not configured",
        )

    if mode == "subscribe" and verify_token == expected_token:
        return {"hub.challenge": challenge}

    raise HTTPException(status_code=403, detail="Invalid verification token")

def _event_is_authentic(event: "StravaEvent", db: Session) -> bool:
    """Authenticate an incoming webhook event.

    Strava does not sign webhook payloads, and the endpoint is exempt from
    basic auth so the verification handshake can reach it. Anyone who learns
    the callback URL could otherwise POST a forged but well-formed event to
    enqueue jobs or soft-delete activities (see #100). Guard with two cheap
    equality checks against values we already hold:

    1. The event must reference our active push subscription. Strava only
       returns the subscription id to us at registration, so it is the
       stronger (secret-ish) check. Skipped when unconfigured (id 0) so local
       dev is not blocked.
    2. The event owner must be a connected athlete. Athlete ids are public, so
       this check is weaker on its own, but it is always available from the DB
       and never drifts, so it stays on even when (1) is unconfigured.
    """
    expected_subscription_id = settings.STRAVA_WEBHOOK_SUBSCRIPTION_ID
    if expected_subscription_id and event.subscription_id != expected_subscription_id:
        logger.warning(
            "strava_webhook_rejected_subscription_mismatch",
            extra={
                "event_subscription_id": event.subscription_id,
                "object_id": event.object_id,
            },
        )
        return False

    account = db.execute(
        select(StravaAccount).where(
            StravaAccount.strava_athlete_id == event.owner_id
        )
    ).scalars().first()
    if account is None:
        logger.warning(
            "strava_webhook_rejected_unknown_owner",
            extra={"owner_id": event.owner_id, "object_id": event.object_id},
        )
        return False

    return True


@router.post("/webhooks/strava")
async def receive_webhook(
    event: StravaEvent,
    db: Session = Depends(get_db)
):
    """
    Handle incoming events from Strava.
    """
    if not _event_is_authentic(event, db):
        # Reject before any side effect (enqueue or soft-delete). A forged
        # sender is not Strava, so a 403 here does not affect the real
        # subscription's health.
        raise HTTPException(status_code=403, detail="Unauthenticated webhook event")

    if event.object_type != "activity":
        # We assume we only care about activities for now
        return {"status": "ignored", "reason": "not_activity"}

    if event.aspect_type == "delete":
        # Soft delete: mark the activity deleted and commit, then remove it from
        # its Block so it no longer shapes bounds, holds the primary anchor,
        # counts in the opener debounce, or attracts late arrivals (#238, ADR 0011).
        stmt = update(Activity).where(
            Activity.strava_activity_id == event.object_id
        ).values(is_deleted=True)
        db.execute(stmt)
        db.commit()
        activity = (
            db.query(Activity)
            .filter(Activity.strava_activity_id == event.object_id)
            .first()
        )
        if activity is not None and activity.block_id is not None:
            try:
                remove_activity_from_block(db, activity)
            except Exception:
                # Block removal failed: the soft-delete already committed, so
                # the activity is correctly hidden from users. The block
                # membership is stale but not user-visible; it will not cause
                # data loss. Log and continue.
                logger.exception(
                    "strava_delete_block_removal_failed strava_id=%s",
                    event.object_id,
                )
        return {"status": "processed", "action": "deleted"}

    elif event.aspect_type == "create":
        # Fresh activity: run the full pipeline (ingest → analyze → coach → notify).
        job_id = f"pipeline_{event.object_id}_{event.event_time}"
        queue.enqueue(
            process_new_activity_job,
            strava_athlete_id=event.owner_id,
            strava_activity_id=event.object_id,
            job_id=job_id,
            result_ttl=3600,
            retry=PIPELINE_RETRY,
        )
        return {"status": "processed", "action": "enqueued_pipeline"}

    elif event.aspect_type == "update":
        # Existing activity edit (title, visibility): re-ingest only.
        job_id = f"sync_{event.object_id}_{event.event_time}"
        queue.enqueue(
            sync_activity_job,
            strava_athlete_id=event.owner_id,
            strava_activity_id=event.object_id,
            job_id=job_id,
            result_ttl=3600,
        )
        return {"status": "processed", "action": "enqueued_sync"}

    return {"status": "ignored", "reason": "unknown_aspect"}


# --- Telegram inbound callback (I1b, #220) -------------------------------------
# The runner taps an RPE/pain button on the opener message in Telegram; Telegram
# POSTs a callback_query here. The endpoint is BasicAuth-exempt (the /api/webhooks
# prefix) so Telegram can reach it, so it must authenticate the request itself
# before any side effect, mirroring the Strava-webhook posture above.


class _TgChat(BaseModel):
    id: int = 0


class _TgMessage(BaseModel):
    chat: _TgChat = _TgChat()
    message_id: int = 0
    reply_markup: dict | None = None


class _TgCallbackQuery(BaseModel):
    id: str
    data: str | None = None
    message: _TgMessage | None = None


class TelegramUpdate(BaseModel):
    # Telegram sends every update type to one webhook URL; we only act on
    # callback_query (a button tap) and ignore the rest. Extra fields are dropped.
    update_id: int = 0
    callback_query: _TgCallbackQuery | None = None


def _callback_is_authentic(
    secret_header: str | None, callback: _TgCallbackQuery
) -> bool:
    """Authenticate an inbound Telegram callback before any side effect.

    Two checks, mirroring the Strava posture:

    1. The `X-Telegram-Bot-Api-Secret-Token` header must equal our configured
       secret. Telegram echoes the `secret_token` set at webhook registration on
       every request, so it is the stronger (secret) check. When the secret is
       unconfigured the check is skipped for local dev, but it fails closed in
       production so an empty secret never silently opens the endpoint.
    2. The callback's chat id must match our configured TELEGRAM_CHAT_ID. Chat
       ids are guessable, so this is the weaker check, but it is always available
       and ties the action to the one connected runner.
    """
    expected_secret = settings.TELEGRAM_WEBHOOK_SECRET
    if expected_secret:
        if secret_header != expected_secret:
            logger.warning("telegram_callback_rejected_secret_mismatch")
            return False
    elif settings.APP_ENV == "production":
        logger.critical("telegram_webhook_secret_missing_in_production")
        return False

    chat_id = callback.message.chat.id if callback.message else 0
    expected_chat = str(settings.TELEGRAM_CHAT_ID).strip()
    if not expected_chat or str(chat_id) != expected_chat:
        logger.warning(
            "telegram_callback_rejected_chat_mismatch",
            extra={"chat_id": chat_id},
        )
        return False

    return True


def _answer_callback(callback_query_id: str, *, text: str = "") -> None:
    """Best-effort acknowledge the tap so Telegram clears the button spinner.

    Only the Telegram notifier can answer; any other active channel (email,
    no-op, in-memory) has no callback to answer, so this is a guarded no-op
    there. Never raises into the request path."""
    notifier = get_notifier()
    answer = getattr(notifier, "answer_callback", None)
    if answer is None:
        return
    try:
        answer(callback_query_id, text=text)
    except Exception:
        logger.exception("failed to answer telegram callback %s", callback_query_id)


_TAP_MARK = "✓ "


def _mark_tapped_button(callback: _TgCallbackQuery) -> None:
    """Persistently acknowledge a tap in-chat (#230): re-render the opener's
    inline keyboard with the chosen button check-marked. A re-tap moves the mark
    (each pass strips old marks first). Only the keyboard is edited — editing the
    text would strip the HTML title/link, which Telegram does not echo back in
    the callback payload. Best-effort: never raises into the request path, and
    skips entirely when the callback carries no keyboard/message id or the active
    notifier cannot edit (email, no-op)."""
    msg = callback.message
    if msg is None or not msg.message_id or not msg.reply_markup:
        return
    edit = getattr(get_notifier(), "edit_message_reply_markup", None)
    if edit is None:
        return

    keyboard = msg.reply_markup.get("inline_keyboard") or []
    marked = []
    for row in keyboard:
        new_row = []
        for button in row:
            label = (button.get("text") or "").removeprefix(_TAP_MARK)
            if button.get("callback_data") == callback.data:
                label = _TAP_MARK + label
            new_row.append({**button, "text": label})
        marked.append(new_row)
    if marked == keyboard:
        return  # same button re-tapped: Telegram rejects a no-change edit

    try:
        edit(message_id=msg.message_id, reply_markup={"inline_keyboard": marked})
    except Exception:
        logger.exception(
            "failed to mark tapped telegram button on message %s", msg.message_id
        )


@router.post("/webhooks/telegram")
def receive_telegram_callback(
    update: TelegramUpdate,
    db: Session = Depends(get_db),
    secret_header: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    """Handle a runner tapping an RPE/pain button on the Telegram opener (I1b).

    Authenticate first, then turn the tap into the same CheckIn the in-app path
    writes (via the shared `write_checkin`), which also fires the A4 fuller turn
    when the exchange is open. Always returns 200 to a malformed-but-authentic or
    non-callback update so Telegram does not retry; rejects an unauthenticated
    request with 403 before any side effect."""
    callback = update.callback_query
    if callback is None:
        # Not a button tap (a plain message, etc.): nothing to do.
        return {"status": "ignored", "reason": "not_callback"}

    if not _callback_is_authentic(secret_header, callback):
        raise HTTPException(status_code=403, detail="Unauthenticated callback")

    action = decode_callback_token(callback.data or "")
    if action is None:
        logger.warning("telegram_callback_unparseable_token")
        _answer_callback(callback.id)
        return {"status": "ignored", "reason": "bad_token"}

    try:
        activity_uuid = uuid.UUID(action.activity_id)
    except ValueError:
        _answer_callback(callback.id)
        return {"status": "ignored", "reason": "bad_activity"}

    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if activity is None:
        _answer_callback(callback.id)
        return {"status": "ignored", "reason": "unknown_activity"}

    if action.kind == "done":
        # #296: the "done" tap is a control signal, not a CheckIn — record the
        # explicit completion and schedule the full report (~30 min). No CheckIn is
        # written. A no-op (closed/stale exchange, or not the receipt cadence) still
        # acks so the runner's button clears.
        mark_done_and_schedule(db, activity_uuid)
        _mark_tapped_button(callback)
        _answer_callback(callback.id, text="Got it — I'll send your full report shortly.")
        return {"status": "processed", "action": "done_scheduled"}

    # RPE/pain: build the validated CheckIn payload from the token (range-checked
    # the same as the in-app path), then write through the shared helper. An
    # authentic-but-forged callback can still carry an out-of-range value (#504),
    # which CheckInCreate's schema bounds reject; treat that like any other bad
    # callback — ack and ignore (200, no write) so Telegram does not retry on a 500.
    try:
        checkin_data = CheckInCreate(**{action.field: action.value})
    except ValidationError:
        logger.warning(
            "telegram_callback_out_of_range_value",
            extra={"kind": action.kind, "value": action.value},
        )
        _answer_callback(callback.id)
        return {"status": "ignored", "reason": "bad_value"}
    write_checkin(db, activity_uuid, checkin_data)

    label = "RPE" if action.kind == "rpe" else "pain"
    _mark_tapped_button(callback)
    _answer_callback(callback.id, text=f"Got it — {label} {action.value} recorded.")
    return {"status": "processed", "action": "checkin_written"}
