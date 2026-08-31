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
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
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
from app.services.schedule.amend import MAX_AMEND_WEEKS

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "coach-action:"
_TOKEN_TTL_SECONDS = 1800
_TOKEN_MAX_LENGTH = 64

# The action whose confirm does not WRITE anything. `draft_plan` hands a
# generation to the worker and returns, so the change is asked for here and made
# a minute later somewhere else, if it is made at all.
#
# It is named because #778's ledger records writes rather than taps: "written
# only after the change has actually been made". Recording it at confirm time
# records an intention as an outcome, and the coach reads that list back under
# "ALREADY IN THEIR RECORD - what this conversation has written". Observed live:
# a runner confirmed a hill session into next week, the transcript showed it
# done, the job sat unprocessed, and the week held no hill session. Its trace is
# written by the job instead, on success, so the ledger stays true rather than
# needing a caveat that says when to disbelieve it.
#
# `amend_plan` rejoined this set in #998, having left it in #987. Settling the
# week before the card meant generating a plan inside the chat request, and the
# request has a ceiling it cannot be told about: the runner's turn reaches the
# offer with whatever is left of it, which in production was between 10 and 35
# seconds of a 42-second budget, because the rounds and tool calls before the
# offer are variable and often cost more than the generation itself. Under that,
# every window was refused, and the refusal asked the runner to send the request
# again, which refused again. Six messages, no sessions.
#
# On the worker there is no ceiling to run out of, so the amendment is generated
# with its full retry budget and written when it is right.
DEFERRED_ACTION_TYPES = frozenset({"draft_plan", "amend_plan"})

# The actions whose CONTENT has to be settled before the card can be honest
# (#987). Empty, and kept rather than removed: settling first is the better
# design and the machinery below is one entry away from being live again, once
# an amendment can be settled off the request and its card delivered when ready
# (#998). What made the card dishonest was never its timing, it was listing
# sessions that had not been generated. A card naming the ASK - "write the week
# of Aug 31" - promises nothing it cannot keep, so the confirm step survives.
PREPARED_ACTION_TYPES: frozenset = frozenset()


def needs_preparation(tool_input: Dict[str, Any]) -> bool:
    """Whether this offer has to be worked out before it can be minted.

    Reads the raw tool input rather than a parsed request, because it is asked
    before validation: the caller only needs to know whether to announce the wait
    and await `prepare_offer`, and an off-shape input is refused by the mint a
    moment later either way.
    """
    return (tool_input or {}).get("action_type") in PREPARED_ACTION_TYPES


class PreparedOffer(BaseModel):
    """The settled content of an offer, handed from `prepare_offer` to the mint.

    Opaque to the tool loop on purpose. The loop knows only that some offers need
    preparing and that preparation can refuse; what is inside belongs to the
    action, so adding a second prepared action later touches this module and not
    the conversation.
    """

    action_type: str
    ok: bool
    # The refusal, in terms the coach can say back to the runner. This is the
    # honest answer to an impossible request and it is why refusing no longer
    # means silence: it comes back as a tool result the model must respond to,
    # while the runner is still in the conversation, before anything is written.
    detail: Optional[str] = None
    amended_plan: Optional[Dict[str, Any]] = None
    changes: List[str] = Field(default_factory=list)
    week: List["ProposedSessionRow"] = Field(default_factory=list)
    start: Optional[date] = None
    end: Optional[date] = None

    model_config = ConfigDict(extra="forbid")


async def prepare_offer(
    db: Session,
    owner_user_id: UUID,
    tool_input: Dict[str, Any],
    *,
    budget_seconds: Optional[float] = None,
) -> Optional[PreparedOffer]:
    """Work out what an offer would actually do, before the runner is asked.

    Never raises: a preparation that fails comes back `ok=False` with something
    the coach can say, because the failure IS the answer here. A request that
    cannot be satisfied under the runner's own rules should produce an
    explanation and alternatives in the conversation, not a card that cannot be
    honoured and not a silence half a minute later.

    `budget_seconds` is how much of the carrying request's wall-clock is left
    (#995). The silence this docstring promised not to produce is exactly what
    shipped: preparation was given no ceiling, a wide window took longer than the
    platform allows a request to live, and the turn was severed with the runner
    still watching a status label. Passing the budget down is what makes the
    promise structural — too wide a window is refused in time to SAY so.
    """
    try:
        request = ProposedActionRequest.model_validate(tool_input or {})
    except ValidationError:
        # Let the mint produce the shape error, so there is one place that
        # reports an off-contract offer to the model.
        return None
    if request.action_type != "amend_plan":
        return None

    from app.services.schedule import store as schedule_store
    from app.services.schedule.amend import propose_amendment

    if not settings.SCHEDULE_ENABLED:
        return PreparedOffer(
            action_type="amend_plan", ok=False, detail="the schedule is unavailable"
        )
    plan = schedule_store.get_active_plan(db, owner_user_id)
    if plan is None:
        return PreparedOffer(
            action_type="amend_plan",
            ok=False,
            detail="this runner has no active plan to amend; offer draft_plan instead",
        )
    user = plan.user if getattr(plan, "user", None) is not None else None
    if user is None:
        from app.models.user import User

        user = db.query(User).filter(User.id == owner_user_id).first()
    if user is None:
        return PreparedOffer(
            action_type="amend_plan", ok=False, detail="this runner is gone"
        )

    proposal = await propose_amendment(
        db,
        user,
        plan,
        weeks_from=request.weeks_from,
        weeks_through=request.weeks_through,
        instruction=request.amend_reason or "",
        budget_seconds=budget_seconds,
    )
    if not proposal.ok or proposal.amended is None:
        return PreparedOffer(
            action_type="amend_plan",
            ok=False,
            detail="; ".join(proposal.failures or ["the amendment could not be written"]),
            start=proposal.start,
            end=proposal.end,
        )

    surviving = _titles_in_window(db, owner_user_id, plan, proposal.start, proposal.end)
    return PreparedOffer(
        action_type="amend_plan",
        ok=True,
        amended_plan=proposal.amended.model_dump(mode="json"),
        changes=proposal.changes,
        week=[
            ProposedSessionRow(
                date=session.window_start,
                title=session.title,
                intent=session.intent,
                discipline=session.discipline,
                distance_m=session.target_distance_m,
                duration_s=session.target_duration_s,
                changed=(session.window_start, session.title) not in surviving,
            )
            for week in proposal.amended.weeks
            for session in sorted(week.sessions, key=lambda s: s.window_start)
        ],
        start=proposal.start,
        end=proposal.end,
    )


def _titles_in_window(db: Session, owner_user_id: UUID, plan, start, end) -> set:
    """What the window holds today, as (day, title), for marking what changed.

    Compared by day and title, the pair `_describe_change` compares after a
    write, so the card marks a session as new on exactly the terms the ledger
    would later report it as added.
    """
    from app.services.schedule.amend import sessions_in_window

    if start is None or end is None:
        return set()
    return {
        (row.window_start, row.title)
        for row in sessions_in_window(db, owner_user_id, plan, start, end)
    }

class ProposedActionFrame(BaseModel):
    action_type: Literal[
        "check_in",
        "intent",
        "split_block",
        "merge_blocks",
        "complete_session",
        "draft_plan",
        "adjust_session",
        "revise_max_hr",
        "amend_plan",
    ]
    token: str
    description: str
    confirm_label: str
    dismiss_label: str = "Leave it"
    # What the runner is agreeing to, shown rather than forecast (#987). Both
    # are server-derived from the settled amendment: `changes` is the difference
    # in the same words the ledger uses afterwards, `week` is the whole window as
    # it would stand. Only `amend_plan` fills them, because it is the only offer
    # whose content is decided rather than named.
    changes: List[str] = Field(default_factory=list)
    week: List["ProposedSessionRow"] = Field(default_factory=list)


class ProposedSessionRow(BaseModel):
    """One session on the card, as the runner would have it.

    Deliberately display-only and flat. It exists so the sheet can render the
    proposed window without teaching the client the schedule's shape, and it
    carries no id: nothing here is a row yet.
    """

    date: date
    title: str
    intent: str
    discipline: str
    distance_m: Optional[float] = None
    duration_s: Optional[int] = None
    # Whether this session is new to the window, so the sheet can mark it.
    changed: bool = False


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
        "adjust_session",
        "revise_max_hr",
        "amend_plan",
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
    # #881: the corrected prescription, for `adjust_session`. Bounded here as
    # well as in the writer, so an absurd value never reaches a card the runner
    # could tap — the drafted-session contract's own limits, because a correction
    # is not a looser channel than the one that wrote the session.
    target_distance_m: Optional[float] = Field(default=None, gt=0, le=200_000)
    target_duration_s: Optional[int] = Field(default=None, gt=0, le=86_400)
    # #981: the window an amendment rewrites, as WEEK OFFSETS from now, never as
    # dates. The server resolves them against the runner's own week boundary
    # (`amend.resolve_window`), the `ScreenPointer` discipline applied to a
    # write: a model-supplied date can be a week wrong with nothing noticing,
    # and this one decides which of the runner's sessions get overwritten.
    weeks_from: Optional[int] = Field(default=None, ge=0, le=11)
    weeks_through: Optional[int] = Field(default=None, ge=0, le=11)
    # What the amendment is for, in the coach's own words. It goes onto the card
    # the runner reads AND into the prompt that writes the sessions, so the
    # change the runner agrees to and the change that gets made are described by
    # one sentence rather than two that could differ.
    amend_reason: Optional[str] = Field(default=None, min_length=1, max_length=300)

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
        elif self.action_type == "adjust_session":
            if self.planned_session_id is None:
                raise ValueError("adjust_session requires planned_session_id")
            # Exactly one. Both is two prescriptions for one session; neither is
            # a card that would say only "change this session", which is not
            # something a runner can agree to.
            if (self.target_distance_m is None) == (self.target_duration_s is None):
                raise ValueError(
                    "adjust_session requires target_distance_m or target_duration_s, "
                    "not both"
                )
        elif self.action_type == "amend_plan":
            if self.weeks_from is None or self.weeks_through is None:
                raise ValueError(
                    "amend_plan requires weeks_from and weeks_through"
                )
            if self.weeks_through < self.weeks_from:
                raise ValueError("weeks_through is before weeks_from")
            if (self.weeks_through - self.weeks_from) + 1 > MAX_AMEND_WEEKS:
                # The bound is what keeps "amend" from quietly becoming
                # "redraft". An amendment promises the rest of the plan is
                # untouched, and a window wide enough to swallow the block is
                # not making that promise in good faith.
                raise ValueError(
                    f"an amendment may span at most {MAX_AMEND_WEEKS} weeks"
                )
            if not self.amend_reason:
                # The card has to say WHY as well as what. "Change your next two
                # weeks" is not something a runner can agree to.
                raise ValueError("amend_plan requires amend_reason")
        # The correction's arguments belong to the correction. Riding along on
        # another action they would be silently dropped, and an instruction the
        # coach wrote and nothing stored is the shape #878 was raised for.
        if self.action_type != "adjust_session" and (
            self.target_distance_m is not None or self.target_duration_s is not None
        ):
            raise ValueError("only adjust_session takes a corrected target")
        if self.action_type != "amend_plan" and (
            self.weeks_from is not None
            or self.weeks_through is not None
            or self.amend_reason is not None
        ):
            raise ValueError("only amend_plan takes a window and a reason")
        # `revise_max_hr` (#945) deliberately takes NO arguments, the
        # `draft_plan` precedent: the evidence and the proposed number are
        # deterministic facts already in front of the coach in THE RUNNER
        # section, re-derived fresh from the runner's own data at offer time
        # rather than trusted from the model. There is no field here for the
        # model to invent a number into.
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
        "adjust_session",
        "revise_max_hr",
        "amend_plan",
    ]
    activity_id: Optional[UUID] = None
    rpe: Optional[int] = None
    pain_score: Optional[int] = None
    user_intent: Optional[str] = None
    block_id: Optional[UUID] = None
    split_at_activity_id: Optional[UUID] = None
    other_block_id: Optional[UUID] = None
    planned_session_id: Optional[UUID] = None
    target_distance_m: Optional[float] = None
    target_duration_s: Optional[int] = None
    # #981: the amendment's window and its reason, carried to the worker that
    # writes it. Offsets rather than dates for the same reason they are offsets
    # on the request, and resolved once more at execute time so a card confirmed
    # after midnight amends the week the runner is actually in.
    weeks_from: Optional[int] = None
    weeks_through: Optional[int] = None
    amend_reason: Optional[str] = None
    # #987: the settled amendment itself, decided BEFORE the card went up and
    # written verbatim when the runner taps. This is what makes the tap mean what
    # the card said: with the week already chosen, confirming has nothing left to
    # decide, and the retry loop that once turned a refusal into a substitution
    # has no run at confirm time to do it in.
    #
    # Stored as the serialized `AmendedPlan`, beside the window it was proposed
    # for so a token confirmed after midnight cannot land its weeks on different
    # dates than the ones the runner read.
    amended_plan: Optional[Dict[str, Any]] = None
    amend_start: Optional[date] = None
    amend_end: Optional[date] = None
    # The plan the offer was minted AGAINST, the `stated_max_hr_at_offer`
    # precedent. A plan can be replaced or restored in the half hour a token
    # lives, and an amendment aimed at one block must not silently land on a
    # different one.
    plan_id_at_offer: Optional[UUID] = None
    # #945: the revised max HR, in bpm. Always server-computed at offer time
    # from `max_hr_calibration.gather_max_hr_revision` — never model-supplied,
    # since there is no field on `ProposedActionRequest` for `revise_max_hr`.
    proposed_max_hr: Optional[int] = None
    # #945: the stated max HR this offer was minted AGAINST. A review
    # demonstrated that re-deriving "does some revision still hold" at
    # confirm time is the wrong check — it is satisfied even after the
    # runner has since corrected their own profile to a different number by
    # hand, and the code would silently overwrite that deliberate edit with
    # the stale offered value. The only question that protects the write is
    # whether the profile is STILL the exact number this offer was built
    # against; that requires remembering what it was.
    stated_max_hr_at_offer: Optional[int] = None
    # #856: the conversation the plan was settled in. Server-supplied at mint
    # time, never model-supplied. Carried by EVERY action since #778, because a
    # confirmed change leaves its trace in the conversation that reached it.
    thread_id: Optional[UUID] = None
    # #778: the card's own words, so the trace records what the runner actually
    # agreed to rather than a second description of it. Server-supplied.
    description: Optional[str] = None


PROPOSED_ACTION_TOOL: Dict[str, Any] = {
    "name": "offer_proposed_action",
    "description": (
        "Offer ONE narrow, reversible change to the runner's record for them to "
        "confirm: logging a check-in (RPE and/or pain), naming a session's stated "
        "intent, splitting a block at a named member session, merging two "
        "adjacent blocks, marking a PLANNED session done when the runner says "
        "they did it (the gym and the turbo never reach Strava, so a session they "
        "mention is often the only record there will be), correcting how far or "
        "how long ONE planned session should be (adjust_session — for a session "
        "whose prescription is wrong, so a single number does not cost them the "
        "whole block; it changes nothing else about the session, and their day, "
        "intent and discipline are not yours to change this way), or writing a "
        "block of training you have settled together into their schedule "
        "(draft_plan — takes no arguments; this conversation IS the plan, so use "
        "it once the shape of the block is agreed rather than asking them to copy "
        "it out), or rewriting a FEW WEEKS of the plan they already have while "
        "the rest of it stays exactly as it is (amend_plan - use this whenever "
        "the plan needs to change but does not need replacing: they are sore and "
        "this week should soften, they want a session added or dropped, or their "
        "written weeks have run out and the next block should be built from the "
        "shape you already agreed. Prefer it over draft_plan every time: "
        "draft_plan throws away the block they agreed to and writes a different "
        "one, so it is for starting over, not for changing your mind), "
        "or updating their stated max heart rate when their own recent "
        "training has clearly overtaken it (revise_max_hr — takes no arguments; "
        "the evidence and the proposed number are already in front of you in "
        "THE RUNNER section when this applies, so offer it there rather than "
        "asking them to state a new number, and never claim you have already "
        "changed it). "
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
                    "adjust_session",
                    "revise_max_hr",
                    "amend_plan",
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
                    "The planned session this is about: the one the runner has "
                    "just said they did, or the one whose prescription is wrong. "
                    "Use only an id already in front of you."
                ),
            },
            "target_distance_m": {
                "type": "number",
                "description": (
                    "adjust_session only: what the WHOLE session should be, in "
                    "metres, door to door. Give this or target_duration_s, not "
                    "both."
                ),
            },
            "target_duration_s": {
                "type": "integer",
                "description": (
                    "adjust_session only: what the whole session should be, in "
                    "seconds, when it is prescribed by time rather than distance."
                ),
            },
            "weeks_from": {
                "type": "integer",
                "description": (
                    "amend_plan only: the first week to rewrite, counted from "
                    "the week the runner is in now. 0 is this week, 1 is next "
                    "week. Never a date."
                ),
            },
            "weeks_through": {
                "type": "integer",
                "description": (
                    "amend_plan only: the last week to rewrite, counted the same "
                    "way, and the same as weeks_from for a single week. Rewriting "
                    "this week only is 0 and 0; this week and next is 0 and 1."
                ),
            },
            "amend_reason": {
                "type": "string",
                "description": (
                    "amend_plan only: why the plan is changing, in one short "
                    "phrase and in the runner's terms. It is shown on the card "
                    "they confirm and it is what the amendment is written from, "
                    "so say the change rather than the situation: 'drop one hard "
                    "session, right calf is sore', 'add a fourth easy run', "
                    "'write the next block out of the agreed shape'."
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
    prepared: Optional["PreparedOffer"] = None,
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

    if needs_preparation(tool_input) and (prepared is None or not prepared.ok):
        # An offer whose content had to be settled and was not, or was and came
        # back refused. Either way there is no card: what the model gets is the
        # reason, so it can tell the runner what does not fit and what would.
        # This is the refusal reaching the runner, in the turn they asked in.
        detail = (
            prepared.detail
            if prepared is not None and prepared.detail
            else "the amendment could not be worked out"
        )
        return {"ok": False, "error": "cannot_amend", "detail": detail}, None

    try:
        frame, stored = _build_offer(
            db, owner_user_id, request, thread_id=thread_id, prepared=prepared
        )
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

    # Stamped here rather than in each `_build_offer` branch, so an action added
    # later carries its conversation and its wording without having to remember.
    stored = stored.model_copy(
        update={
            "thread_id": stored.thread_id or thread_id,
            "description": frame.description,
        }
    )

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
        # `mode="json"` because this frame is serialized straight onto the SSE
        # stream by `json.dumps`, which has no encoder for a `date`. The card's
        # week carries real dates (#987) and a plain dump put `date` objects in
        # a frame the turn then died trying to send, losing the whole reply.
        frame.model_copy(update={"token": token}).model_dump(mode="json"),
    )


def consume_and_execute(db: Session, owner_user_id: UUID, token: str) -> dict:
    """Redeem and execute one confirmed proposed action, or raise on failure."""
    stored = _consume_token(owner_user_id, token)
    if stored is None:
        raise LookupError("Proposed action not found")

    result = _execute(db, owner_user_id, stored)
    if stored.action_type not in DEFERRED_ACTION_TYPES:
        _record_confirmed(db, stored, result)
    return result


def _record_confirmed(
    db: Session, stored: StoredProposedAction, result: Optional[dict] = None
) -> None:
    """Write the confirmation into the conversation that reached it (#778).

    Only after the change has been made: a refused or failed confirm leaves no
    trace, because nothing happened. Fail-soft — the write has already landed on
    the runner's record, and losing the trace is a smaller harm than answering a
    successful confirm with a 500. A token minted before this shipped carries no
    thread or wording and simply records nothing.

    An action that reports what it CHANGED has that recorded beside the card's
    own words (#986). The card is what the runner agreed to and is how they
    recognise the entry, so it stays the opening line; the change is what the
    rows actually did. The two are kept separate here because the whole point is
    that they must be able to disagree in the record if they ever disagree in
    fact — the coach reads this back as "already in their record", and a forecast
    standing in for an outcome is how it came to coach from a session that had
    been deleted rather than written.
    """
    description = stored.description or ""
    changes = (result or {}).get("changes") or []
    if description and changes:
        description = description.rstrip() + " | " + " ".join(changes)
    try:
        from app.services.coach import threads as thread_service

        thread_service.record_action_event(
            db, stored.thread_id, stored.activity_id, description
        )
    except Exception:  # noqa: BLE001 -- the change is made; the trace is not worth a 500
        logger.exception("coach proposed-action trace write failed")


def _execute(db: Session, owner_user_id: UUID, stored: StoredProposedAction) -> dict:
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
            enqueue_draft(
                owner_user_id, existing.id, stored.thread_id, stored.description
            )
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

    if stored.action_type == "amend_plan":
        from app.services.schedule import store as schedule_store

        if not settings.SCHEDULE_ENABLED:
            raise ValueError("the schedule is unavailable")
        plan = schedule_store.get_active_plan(db, owner_user_id)
        if plan is None:
            raise ValueError(
                "you no longer have an active plan, so nothing was changed"
            )
        # The plan must still be the one this card was written against, the
        # `revise_max_hr` precedent and for the same reason: the token lives half
        # an hour, and a draft or a restore in that window makes a DIFFERENT plan
        # current. Amending that one would apply a change the runner agreed for
        # one block to a block they never saw it described against. Refused
        # rather than reasoned about; the coach can offer again from what is now
        # true.
        if stored.plan_id_at_offer is not None and plan.id != stored.plan_id_at_offer:
            raise ValueError(
                "your plan changed since this was offered, so nothing was changed"
            )
        # Handed to the worker (#998). The generation cannot run here: this is a
        # request with a wall-clock ceiling, and an amendment needs more of it
        # than the turn reliably has left. On the worker it gets its full retry
        # budget, which is what recovers the coherence failures a single attempt
        # cannot - the one that made this loop was a strength session written
        # with no duration, exactly what a rewrite is for.
        #
        # The promise this returns is the same one `draft_plan` makes and keeps,
        # and it is kept the same way: the job reports back into this thread,
        # whether it wrote the week or could not (#984). What broke before was a
        # promise with nothing behind it, not a promise made in advance.
        from app.jobs.amend_schedule import enqueue_amendment
        from app.services.schedule import amend_watch

        # Marked BEFORE the enqueue, so the window is watchable from the moment
        # the runner taps rather than from whenever a worker picks the job up
        # (#1003). The gap is small and the screen is wrong for all of it.
        amend_watch.mark_started(owner_user_id, stored.amend_start, stored.amend_end)
        enqueue_amendment(
            owner_user_id,
            plan.id,
            weeks_from=stored.weeks_from or 0,
            weeks_through=stored.weeks_through or 0,
            instruction=stored.amend_reason or "",
            thread_id=stored.thread_id,
            description=stored.description,
        )
        return {
            "action_type": "amend_plan",
            "plan_id": str(plan.id),
            "message": (
                "Working it out now — I'll put it on your Schedule screen and "
                "tell you here when it's in."
            ),
        }

    if stored.action_type == "adjust_session":
        from app.services.schedule.adjust import adjust_planned_session

        if not settings.SCHEDULE_ENABLED:
            raise ValueError("the schedule is unavailable")
        session = _require_owned_planned_session(
            db, owner_user_id, stored.planned_session_id
        )
        # Ownership re-resolved at execute time like every other action here, and
        # the writer re-checks every refusal the offer already checked: the token
        # lives for half an hour, and a session can be completed or fall into the
        # past between the card going up and the runner tapping it.
        adjust_planned_session(
            db,
            session,
            distance_m=stored.target_distance_m,
            duration_s=stored.target_duration_s,
        )
        return {
            "action_type": "adjust_session",
            "planned_session_id": str(session.id),
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

    if stored.action_type == "revise_max_hr":
        from app.models.user_profile import UserProfile

        # Ownership re-resolved from the authenticated owner, like every other
        # action here — there is no id in the payload to mistrust because a
        # profile is a 1:1 owner resource, so the query is scoped by
        # `owner_user_id` alone.
        profile = (
            db.query(UserProfile).filter(UserProfile.user_id == owner_user_id).first()
        )
        if profile is None:
            raise LookupError("Proposed action not found")
        # Re-checked fresh at execute time, the `adjust_session` precedent —
        # but the check that matters is an EXACT match, not "does some
        # revision still hold". A review demonstrated the difference: stated
        # 180 -> offer minted at 193 -> the runner edits their own profile to
        # 150 through the ordinary profile screen -> "does some revision
        # still hold against 150" is TRUE (150 is even further below the
        # observed peaks), so that question silently overwrote the runner's
        # own deliberate correction with the stale 193. The only question
        # that actually protects the write is whether the profile is STILL
        # the exact number this offer was built against; if it has moved at
        # all, the offer no longer describes a real transition and is
        # refused rather than reasoned about further — the coach can always
        # re-offer from current evidence.
        if profile.max_hr != stored.stated_max_hr_at_offer:
            raise ValueError(
                "the runner's stated max HR has changed since this offer was "
                "made; nothing was changed"
            )
        profile.max_hr = stored.proposed_max_hr
        profile.max_hr_source = "runner_confirmed"
        # The anti-nag stamp is moot once the fact itself has moved; clearing
        # it keeps a stale "last surfaced" value from outliving the number it
        # was about.
        profile.max_hr_revision_last_surfaced_value = None
        profile.max_hr_revision_last_surfaced_at = None
        db.commit()
        return {"action_type": "revise_max_hr", "max_hr": profile.max_hr}

    raise LookupError("Proposed action not found")


def _describe_applied(changes: List[str]) -> str:
    """What the confirm says back, once the change has actually been made.

    Past tense, because by the time this is read the sessions exist. It leads
    with the difference rather than the promise: the runner saw this same line on
    the card, so reading it back is how they know the two agree, and a line that
    does NOT match what they agreed is the one thing they most need to see.
    """
    if not changes:
        return "Done. Nothing in those weeks needed to change."
    return "Done. " + " ".join(changes) + " Everything else in your plan is as it was."


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


def _replace_description(existing, describe_age) -> str:
    """What this card will write over, said plainly enough to stop a mistake."""
    if existing is None:
        return "Write this plan into your schedule"
    age = describe_age(existing)
    if age is None:
        return "Write this plan into your schedule, replacing your current one"
    return f"Write this plan into your schedule, replacing the one written {age}"


def _profile_for(db: Session, owner_user_id: UUID):
    """The runner's profile, for the week boundary an amendment resolves against."""
    from app.models.user_profile import UserProfile

    return (
        db.query(UserProfile).filter(UserProfile.user_id == owner_user_id).first()
    )


def _describe_amendment(start: date, end: date, reason: Optional[str]) -> str:
    """What the amendment card says: the reason, the window, and the promise.

    All three, because the promise is the point. An amendment is worth having as
    a separate action from `draft_plan` precisely because it leaves the rest of
    the plan alone, and a card that did not say so would be asking the runner to
    take that on trust — which is how a runner came to confirm a second draft
    and lose the block they had agreed ninety seconds earlier (#883).
    """
    if start == end:
        window = start.strftime("%a %-d %b")
    elif start.month == end.month:
        window = f"{start.strftime('%-d')}-{end.strftime('%-d %b')}"
    else:
        window = f"{start.strftime('%-d %b')} to {end.strftime('%-d %b')}"
    reason_text = " ".join((reason or "").split()) or "Rewrite these weeks"
    return (
        f"{reason_text[0].upper()}{reason_text[1:]} ({window}). "
        f"The rest of your plan, its rules and your race stay as they are."
    )


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
    prepared: Optional[PreparedOffer] = None,
) -> tuple[ProposedActionFrame, StoredProposedAction]:
    if request.action_type == "draft_plan":
        from app.services.schedule import store as schedule_store

        if not settings.SCHEDULE_ENABLED:
            # The surface's kill switch reaches the offer too. Putting a card up
            # for a screen that answers 503 would be a promise the app cannot keep.
            raise ValueError("the schedule is unavailable")
        from app.services.schedule.coach_view import describe_written_ago

        existing = schedule_store.get_active_plan(db, owner_user_id)
        frame = ProposedActionFrame(
            action_type="draft_plan",
            token="",
            # The replacement is named on the card, not left to be discovered.
            # Writing a plan supersedes the one the runner is training to, and
            # that is the part of this action they most need to see before it
            # happens — the runner-confirms-before-anything-is-written property
            # is only worth having if the card says what will be written over.
            #
            # And named with its AGE (#883). A runner asked "Is it added?", was
            # offered a second card, and confirmed it — replacing the plan they
            # had accepted ninety seconds earlier. Drafting is not deterministic,
            # so the replacement was a different plan. "Your current one" was
            # true and said nothing about the part that would have stopped them:
            # that the plan in question was the one they had just agreed.
            description=_replace_description(existing, describe_written_ago),
            confirm_label="Put it in my schedule",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="draft_plan",
            thread_id=thread_id,
        )
        return frame, stored

    if request.action_type == "amend_plan":
        from app.services.schedule import store as schedule_store

        if not settings.SCHEDULE_ENABLED:
            # The surface's kill switch reaches this write path too, exactly as
            # it reaches `draft_plan` and `adjust_session`.
            raise ValueError("the schedule is unavailable")
        plan = schedule_store.get_active_plan(db, owner_user_id)
        if plan is None:
            raise ValueError(
                "this runner has no active plan to amend; offer draft_plan instead"
            )
        # The window is resolved from the runner's own week start, never from
        # dates the model supplied, exactly as `propose_amendment` resolves it.
        # The card and the job then agree about which weeks are in play without
        # either of them having to be told by the other.
        from app.services.schedule.amend import resolve_window
        from app.services.weeks import resolve_week_start

        start, end = resolve_window(
            date.today(),
            resolve_week_start(getattr(plan.user, "profile", None)),
            weeks_from=request.weeks_from,
            weeks_through=request.weeks_through,
        )
        frame = ProposedActionFrame(
            action_type="amend_plan",
            token="",
            # The card names the WINDOW and what is left alone (#883's lesson,
            # applied to the smaller verb). It carries no session list, because
            # nothing has been written yet and a card listing sessions that do
            # not exist is the dishonesty #987 was right about. Naming the ask is
            # a promise the job can keep.
            description=_describe_amendment(start, end, request.amend_reason),
            confirm_label="Update my plan",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="amend_plan",
            weeks_from=request.weeks_from,
            weeks_through=request.weeks_through,
            amend_reason=request.amend_reason,
            amend_start=start,
            amend_end=end,
            plan_id_at_offer=plan.id,
        )
        return frame, stored

    if request.action_type == "adjust_session":
        from app.services.schedule.adjust import AdjustRefused, describe_adjustment

        if not settings.SCHEDULE_ENABLED:
            # The surface's kill switch reaches this offer too, exactly as it
            # reaches `draft_plan`. It is the fast lever when the schedule
            # misbehaves, and a write path into schedule rows that stays open
            # through it is not a kill switch.
            raise ValueError("the schedule is unavailable")
        session = _require_owned_planned_session(
            db, owner_user_id, request.planned_session_id
        )
        # Refused HERE rather than at confirm time (#881). Every reason a
        # correction can be refused is knowable before the card goes up — the
        # session is done, it has passed, it is a rest day — and a card the
        # runner taps only to be told no is worse than no card: they have already
        # agreed to something that was never going to happen.
        try:
            description = describe_adjustment(
                session,
                distance_m=request.target_distance_m,
                duration_s=request.target_duration_s,
            )
        except AdjustRefused as exc:
            raise ValueError(str(exc)) from exc
        frame = ProposedActionFrame(
            action_type="adjust_session",
            token="",
            description=description,
            confirm_label="Change it",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="adjust_session",
            planned_session_id=session.id,
            target_distance_m=request.target_distance_m,
            target_duration_s=request.target_duration_s,
        )
        return frame, stored

    if request.action_type == "revise_max_hr":
        from app.services.coach.max_hr_calibration import gather_max_hr_revision

        # Re-derived fresh from the runner's own data, never trusted from the
        # model or from what the coach saw earlier in the conversation (#945
        # decision 2): the evidence can have changed, been resolved by a
        # manual profile edit, or already been offered and be sitting inside
        # its cooldown, in the time between the pack being built and this
        # tool call landing.
        finding = gather_max_hr_revision(db, owner_user_id)
        if finding is None:
            raise ValueError(
                "no max HR revision is currently supported by this runner's data"
            )
        frame = ProposedActionFrame(
            action_type="revise_max_hr",
            token="",
            description=(
                f"Update your max heart rate from {finding.stated_max} to "
                f"{finding.suggested_max} bpm — {finding.basis}"
            ),
            confirm_label="Update it",
        )
        stored = StoredProposedAction(
            owner_user_id=owner_user_id,
            action_type="revise_max_hr",
            proposed_max_hr=finding.suggested_max,
            stated_max_hr_at_offer=finding.stated_max,
        )
        # Stamped here, at OFFER time, not from any read path (#945 AC5): this
        # is the one place a `revise_max_hr` card is actually about to reach
        # the runner, so this is the one place the anti-nag cooldown starts.
        # A read that merely shows the fact in context -- including the
        # read-only diagram capture -- never writes.
        from app.services.coach.max_hr_calibration import record_surfaced

        record_surfaced(db, owner_user_id, finding.suggested_max)
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
