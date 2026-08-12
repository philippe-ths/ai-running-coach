"""Thread proposed actions (#768, ADR 0027 property 4).

The coach may OFFER a narrow, reversible action in a thread, but the write only
executes when the runner confirms it. The model chooses which action to offer
and with what arguments; the server validates ownership, mints a single-use
token, and executes the same write path the existing API already uses.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import redis_conn
from app.models import Activity, Block
from app.schemas import CheckInCreate
from app.services import activity_queries
from app.services.blocks import blocks_are_adjacent, merge_blocks, split_block
from app.services.checkins import write_checkin
from app.services.intents import intent_options_for, write_activity_intent

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "coach-action:"
_TOKEN_TTL_SECONDS = 1800
_TOKEN_MAX_LENGTH = 64

class ProposedActionFrame(BaseModel):
    action_type: Literal[
        "check_in",
        "intent",
        "split_block",
        "merge_blocks",
        "complete_session",
        "draft_plan",
    ]
    token: str
    description: str
    confirm_label: str
    dismiss_label: str = "Leave it"


class ProposedActionRequest(BaseModel):
    """The model-facing offer contract.

    Narrow and reversible only. A missing or off-shape argument means no offer.
    """

    action_type: Literal[
        "check_in",
        "intent",
        "split_block",
        "merge_blocks",
        "complete_session",
        "draft_plan",
    ]
    activity_id: Optional[UUID] = None
    rpe: Optional[int] = Field(default=None, ge=1, le=10)
    pain_score: Optional[int] = Field(default=None, ge=0, le=10)
    user_intent: Optional[str] = None
    block_id: Optional[UUID] = None
    split_at_activity_id: Optional[UUID] = None
    other_block_id: Optional[UUID] = None
    # #830: the planned session the conversation settled as done.
    planned_session_id: Optional[UUID] = None

    model_config = ConfigDict(extra="forbid")

    # `draft_plan` (#856) deliberately takes NO arguments. The block lives in the
    # conversation, and the thread id is supplied by the server, so there is no
    # field here a model could get wrong and nothing for it to restate.

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProposedActionRequest":
        if self.action_type == "check_in":
            if self.activity_id is None or (
                self.rpe is None and self.pain_score is None
            ):
                raise ValueError("check_in requires activity_id and rpe or pain_score")
        elif self.action_type == "intent":
            if self.activity_id is None or not self.user_intent:
                raise ValueError("intent requires activity_id and user_intent")
        elif self.action_type == "split_block":
            if self.block_id is None or self.split_at_activity_id is None:
                raise ValueError(
                    "split_block requires block_id and split_at_activity_id"
                )
        elif self.action_type == "merge_blocks":
            if self.block_id is None or self.other_block_id is None:
                raise ValueError("merge_blocks requires block_id and other_block_id")
        elif self.action_type == "complete_session":
            if self.planned_session_id is None:
                raise ValueError("complete_session requires planned_session_id")
        return self


class StoredProposedAction(BaseModel):
    owner_user_id: UUID
    action_type: Literal[
        "check_in",
        "intent",
        "split_block",
        "merge_blocks",
        "complete_session",
        "draft_plan",
    ]
    activity_id: Optional[UUID] = None
    rpe: Optional[int] = None
    pain_score: Optional[int] = None
    user_intent: Optional[str] = None
    block_id: Optional[UUID] = None
    split_at_activity_id: Optional[UUID] = None
    other_block_id: Optional[UUID] = None
    planned_session_id: Optional[UUID] = None
    # #856: the conversation the plan was settled in. Server-supplied at mint
    # time, never model-supplied.
    thread_id: Optional[UUID] = None


PROPOSED_ACTION_TOOL: Dict[str, Any] = {
    "name": "offer_proposed_action",
    "description": (
        "Offer ONE narrow, reversible change to the runner's record for them to "
        "confirm: logging a check-in (RPE and/or pain), naming a session's stated "
        "intent, splitting a block at a named member session, merging two "
        "adjacent blocks, marking a PLANNED session done when the runner says "
        "they did it (the gym and the turbo never reach Strava, so a session they "
        "mention is often the only record there will be), or writing a block of "
        "training you have settled together into their schedule (draft_plan — "
        "takes no arguments; this conversation IS the plan, so use it once the "
        "shape of the block is agreed rather than asking them to copy it out). "
        "Offering is not doing — this puts a card in front of the "
        "runner and nothing is written unless they tap it, so speak of it as "
        "something you can do, never as done. Use only activity_id and block_id "
        "values already in front of you or returned by your other tools; never "
        "invent an id."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action_type": {
                "type": "string",
                "enum": [
                    "check_in",
                    "intent",
                    "split_block",
                    "merge_blocks",
                    "complete_session",
                    "draft_plan",
                ],
            },
            "activity_id": {"type": "string"},
            "rpe": {"type": "integer", "minimum": 1, "maximum": 10},
            "pain_score": {"type": "integer", "minimum": 0, "maximum": 10},
            "user_intent": {
                "type": "string",
                "description": (
                    "The runner's label for what the session was. For a run: "
                    "Easy Run, Recovery, Long Run, Tempo, Intervals, Hills, "
                    "Race, Treadmill. Other activity types take their own "
                    "labels; offer the closest one and the tool will tell you "
                    "the accepted set if it does not fit."
                ),
            },
            "block_id": {"type": "string"},
            "split_at_activity_id": {"type": "string"},
            "other_block_id": {"type": "string"},
            "planned_session_id": {
                "type": "string",
                "description": (
                    "The planned session the runner has just said they did. Use "
                    "only an id already in front of you."
                ),
            },
        },
        "required": ["action_type"],
    },
}


def thread_tools(base_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Thread turns may offer an action; the activity chat box may not."""
    return [*base_tools, PROPOSED_ACTION_TOOL]


def mint_proposed_action(
    db: Session,
    owner_user_id: UUID,
    tool_input: Dict[str, Any],
    *,
    thread_id: Optional[UUID] = None,
) -> tuple[dict, Optional[dict]]:
    """Validate a model-authored offer and mint the user-scoped action token.

    Returns `(model_result, frame)`. The model_result is what the model reads
    back before writing its reply: it says the offer is PENDING and carries no
    token, so the coach can neither report the change as made nor quote the
    runner's one-shot token into prose. The frame — token included — goes to the
    client, which is where the runner's tap comes from. On any failure the frame
    is None and nothing is minted.
    """
    try:
        request = ProposedActionRequest.model_validate(tool_input or {})
    except ValidationError as exc:
        return {"ok": False, "error": "invalid_action", "detail": str(exc)}, None

    try:
        frame, stored = _build_offer(db, owner_user_id, request, thread_id=thread_id)
    except LookupError as exc:
        return {"ok": False, "error": "not_found", "detail": str(exc)}, None
    except _InvalidIntent as exc:
        # A rejection the model can act on: the labels this activity type takes.
        return (
            {
                "ok": False,
                "error": "invalid_action",
                "detail": str(exc),
                "allowed": exc.allowed,
            },
            None,
        )
    except ValueError as exc:
        return {"ok": False, "error": "invalid_action", "detail": str(exc)}, None

    try:
        token = _mint_token(owner_user_id, stored)
    except Exception:  # noqa: BLE001 -- the card is a side affordance, not the reply
        logger.exception("coach proposed-action mint failed")
        return {"ok": False, "error": "unavailable"}, None

    return (
        {
            "ok": True,
            "offered": frame.action_type,
            "shown_as": frame.description,
            "status": (
                "The card is in front of the runner and nothing is written yet — "
                "it carries its own wording and button. Spend your reply coaching "
                "them; do not describe the card or report the change as made."
            ),
        },
        frame.model_copy(update={"token": token}).model_dump(),
    )


def consume_and_execute(db: Session, owner_user_id: UUID, token: str) -> dict:
    """Redeem and execute one confirmed proposed action, or raise on failure."""
    stored = _consume_token(owner_user_id, token)
    if stored is None:
        raise LookupError("Proposed action not found")

    if stored.action_type == "check_in":
        activity = _require_owned_activity(db, owner_user_id, stored.activity_id)
        payload = CheckInCreate(rpe=stored.rpe, pain_score=stored.pain_score)
        write_checkin(db, activity.id, payload)
        return {"action_type": "check_in", "activity_id": str(activity.id)}

    if stored.action_type == "intent":
        activity = _require_owned_activity(db, owner_user_id, stored.activity_id)
        write_activity_intent(db, activity, user_intent=stored.user_intent)
        return {
            "action_type": "intent",
            "activity_id": str(activity.id),
            "user_intent": stored.user_intent,
        }

    if stored.action_type == "split_block":
        block = _require_owned_block(db, owner_user_id, stored.block_id)
        activity = _require_owned_activity(db, owner_user_id, stored.split_at_activity_id)
        _validate_split_offer(db, block, activity)
        left, right = split_block(db, block, at_activity=activity)
        return {
            "action_type": "split_block",
            "block_ids": [str(left.id), str(right.id)],
        }

    if stored.action_type == "merge_blocks":
        block = _require_owned_block(db, owner_user_id, stored.block_id)
        other = _require_owned_block(db, owner_user_id, stored.other_block_id)
        _validate_merge_offer(db, block, other)
        merged = merge_blocks(db, block, other)
        return {"action_type": "merge_blocks", "block_id": str(merged.id)}

    if stored.action_type == "draft_plan":
        from app.services.schedule import store as schedule_store
        from app.services.schedule.draft import enqueue_draft

        if not settings.SCHEDULE_ENABLED:
            raise ValueError("the schedule is unavailable")
        # Idempotent against a draft already running, the `POST /api/schedule/draft`
        # precedent: a second confirm (two devices, a double tap, a stale card)
        # joins the draft in flight rather than starting a second one that would
        # race the first to supersede it.
        existing = schedule_store.draft_in_flight(db, owner_user_id)
        if existing is None:
            existing = schedule_store.create_drafting_plan(db, owner_user_id)
            enqueue_draft(owner_user_id, existing.id, stored.thread_id)
        # The one action whose write does not land where the runner is looking:
        # drafting runs on the worker, so without a word back the card simply
        # disappears and nothing visibly happens for a minute.
        return {
            "action_type": "draft_plan",
            "plan_id": str(existing.id),
            "message": (
                "Writing it into your schedule now — it'll be on your Schedule "
                "screen in a minute."
            ),
        }

    if stored.action_type == "complete_session":
        from app.services.schedule.completion import (
            CONVERSATION,
            complete_planned_session,
        )

        session = _require_owned_planned_session(
            db, owner_user_id, stored.planned_session_id
        )
        # The same writer the tap and the auto-match use. Three routes to done,
        # one write — the `write_checkin` shape.
        complete_planned_session(db, session, source=CONVERSATION)
        return {
            "action_type": "complete_session",
            "planned_session_id": str(session.id),
        }

    raise LookupError("Proposed action not found")


def _require_owned_planned_session(db: Session, owner_user_id: UUID, session_id):
    """Ownership re-resolved at execute time, like every other action here."""
    from app.models.planned_session import PlannedSession

    session = (
        db.query(PlannedSession)
        .filter(
            PlannedSession.id == session_id,
            PlannedSession.user_id == owner_user_id,
        )
        .first()
    )
    if session is None:
        raise LookupError("Proposed action not found")
    return session


def _describe_complete_session(session) -> str:
    when = (
        session.window_start.strftime("%a %-d %b")
        if session.window_start == session.window_end
        else f"{session.window_start.strftime('%-d')}-{session.window_end.strftime('%-d %b')}"
    )
    return f"Mark \u201c{session.title}\u201d ({when}) as done"


def _build_offer(
    db: Session,
    owner_user_id: UUID,
    request: ProposedActionRequest,
    *,
    thread_id: Optional[UUID] = None,
) -> tuple[ProposedActionFrame, StoredProposedAction]:
    if request.action_type == "draft_plan":
        from app.services.schedule import store as schedule_store

        if not settings.SCHEDULE_ENABLED:
            # The surface's kill switch reaches the offer too. Putting a card up
            # for a screen that answers 503 would be a promise the app cannot keep.
            raise ValueError("the schedule is unavailable")
        existing = schedule_store.get_active_plan(db, owner_user_id)
        frame = ProposedActionFrame(
            action_type="draft_plan",
            token="",
            # The replacement is named on the card, not left to be discovered.
            # Writing a plan supersedes the one the runner is training to, and
            # that is the part of this action they most need to see before it
            # happens — the runner-confirms-before-anything-is-written property
            # is only worth having if the card says what will be written over.
            description=(
                "Write this plan into your schedule, replacing your current one"
                if existing is not None
                else "Write this plan into your schedule"
            ),
            confirm_label="Put it in my schedule",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="draft_plan",
            thread_id=thread_id,
        )
        return frame, stored

    if request.action_type == "complete_session":
        session = _require_owned_planned_session(
            db, owner_user_id, request.planned_session_id
        )
        frame = ProposedActionFrame(
            action_type="complete_session",
            token="",
            description=_describe_complete_session(session),
            confirm_label="Mark it done",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="complete_session",
            planned_session_id=session.id,
        )
        return frame, stored

    if request.action_type == "check_in":
        activity = _require_owned_activity(db, owner_user_id, request.activity_id)
        desc = _describe_check_in_offer(activity, request.rpe, request.pain_score)
        frame = ProposedActionFrame(
            action_type="check_in",
            token="",
            description=desc,
            confirm_label="Log it",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="check_in",
            activity_id=activity.id,
            rpe=request.rpe,
            pain_score=request.pain_score,
        )
        return frame, stored

    if request.action_type == "intent":
        activity = _require_owned_activity(db, owner_user_id, request.activity_id)
        intent = _canonical_intent(activity, request.user_intent)
        frame = ProposedActionFrame(
            action_type="intent",
            token="",
            description=f"Mark {_activity_label(activity)} as {intent}",
            confirm_label="Set it",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="intent",
            activity_id=activity.id,
            user_intent=intent,
        )
        return frame, stored

    if request.action_type == "split_block":
        block = _require_owned_block(db, owner_user_id, request.block_id)
        activity = _require_owned_activity(db, owner_user_id, request.split_at_activity_id)
        _validate_split_offer(db, block, activity)
        frame = ProposedActionFrame(
            action_type="split_block",
            token="",
            description=f"Split {_activity_brief(activity)} out from {_block_brief(db, block, exclude_activity_id=activity.id)}",
            confirm_label="Split them",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="split_block",
            block_id=block.id,
            split_at_activity_id=activity.id,
        )
        return frame, stored

    block = _require_owned_block(db, owner_user_id, request.block_id)
    other = _require_owned_block(db, owner_user_id, request.other_block_id)
    _validate_merge_offer(db, block, other)
    frame = ProposedActionFrame(
        action_type="merge_blocks",
        token="",
        description=f"Merge {_block_brief(db, other)} into {_block_brief(db, block)}",
        confirm_label="Merge them",
    )
    stored = StoredProposedAction(
        owner_user_id=owner_user_id,
        action_type="merge_blocks",
        block_id=block.id,
        other_block_id=other.id,
    )
    return frame, stored


def _mint_token(owner_user_id: UUID, stored: StoredProposedAction) -> str:
    token = secrets.token_urlsafe(16)
    redis_conn.set(
        _token_key(owner_user_id, token),
        stored.model_dump_json(),
        ex=_TOKEN_TTL_SECONDS,
    )
    return token


def _consume_token(owner_user_id: UUID, token: str) -> Optional[StoredProposedAction]:
    if not token or len(token) > _TOKEN_MAX_LENGTH:
        return None
    try:
        raw = redis_conn.getdel(_token_key(owner_user_id, token))
    except Exception:  # noqa: BLE001 -- a Redis hiccup must never 500 the confirm
        logger.exception("coach proposed-action consume failed")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    try:
        return StoredProposedAction.model_validate_json(raw)
    except ValidationError:
        return None


def _token_key(owner_user_id: UUID, token: str) -> str:
    return f"{_TOKEN_PREFIX}{owner_user_id}:{token}"


def _require_owned_activity(
    db: Session, owner_user_id: UUID, activity_id: Optional[UUID]
) -> Activity:
    if activity_id is None:
        raise LookupError("Activity not found")
    activity = activity_queries.get_owned_activity(db, activity_id, owner_user_id)
    if activity is None:
        raise LookupError("Activity not found")
    return activity


def _require_owned_block(
    db: Session, owner_user_id: UUID, block_id: Optional[UUID]
) -> Block:
    if block_id is None:
        raise LookupError("Block not found")
    block = (
        db.query(Block)
        .filter(Block.id == block_id, Block.user_id == owner_user_id)
        .first()
    )
    if block is None:
        raise LookupError("Block not found")
    return block


def _canonical_intent(activity: Activity, user_intent: Optional[str]) -> str:
    """The picker's own label for what the coach named, or a rejection.

    The runner's word for a session rarely matches the picker's casing, and the
    coach echoes the runner. Matching loosely and storing the canonical label
    keeps the stated intent one vocabulary with the activity page's selector —
    which now reads the SAME list from `services.intents` rather than a copy of
    it (#779).
    """
    options = intent_options_for(activity.type)
    wanted = " ".join((user_intent or "").split()).casefold()
    for option in options:
        if option.casefold() == wanted:
            return option
    raise _InvalidIntent(options)


class _InvalidIntent(ValueError):
    def __init__(self, allowed: tuple[str, ...]):
        super().__init__("intent is not valid for this activity type")
        self.allowed = list(allowed)


def _validate_split_offer(db: Session, block: Block, activity: Activity) -> None:
    members = (
        db.query(Activity)
        .filter(Activity.block_id == block.id)
        .order_by(Activity.start_date.asc(), Activity.id.asc())
        .all()
    )
    if activity.block_id != block.id:
        raise ValueError("activity is not a member of this block")
    right = [a for a in members if a.start_date >= activity.start_date]
    left = [a for a in members if a.start_date < activity.start_date]
    if not left or not right:
        raise ValueError("split would leave an empty block")


def _validate_merge_offer(db: Session, block: Block, other: Block) -> None:
    if block.id == other.id:
        raise ValueError("cannot merge a block with itself")
    if not blocks_are_adjacent(db, block, other):
        raise ValueError("blocks are not adjacent")


def _activity_label(activity: Activity) -> str:
    day = activity.start_date.strftime("%A")
    if activity.distance_m:
        return f"{day}'s {activity.distance_m / 1000:.1f} km {activity.type.lower()}"
    return f"{day}'s {activity.type.lower()}"


def _activity_brief(activity: Activity) -> str:
    if activity.elapsed_time_s and activity.type.lower() == "walk":
        minutes = round(activity.elapsed_time_s / 60)
        return f"{minutes}-minute walk"
    if activity.distance_m:
        return f"{activity.distance_m / 1000:.1f} km {activity.type.lower()}"
    return activity.type.lower()


def _block_brief(
    db: Session, block: Block, *, exclude_activity_id: Optional[UUID] = None
) -> str:
    members = (
        db.query(Activity)
        .filter(Activity.block_id == block.id)
        .order_by(Activity.start_date.asc(), Activity.id.asc())
        .all()
    )
    if exclude_activity_id is not None:
        members = [m for m in members if m.id != exclude_activity_id]
    if not members:
        return "the block"
    primary = next((m for m in members if m.id == block.primary_activity_id), members[0])
    return _activity_label(primary)


def _describe_check_in_offer(
    activity: Activity, rpe: Optional[int], pain_score: Optional[int]
) -> str:
    target = _activity_label(activity)
    if rpe is not None and pain_score is not None:
        return f"Log {target} as {rpe}/10 effort and {pain_score}/10 pain"
    if rpe is not None:
        return f"Log {target} as {rpe}/10 effort"
    return f"Log {target} as {pain_score}/10 pain"
