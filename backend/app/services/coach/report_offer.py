"""The report's offer (#944) — the offer-and-confirm mechanism, reachable from a report.

The report is where the coach's judgment is sharpest: it has the run that just
happened, the week around it and the plan ahead all in one view. It was also the
one surface where the runner could do nothing with that judgment. "Your Thursday
session should be shorter" was a sentence, and the change was a screen away.

This module owns the three things that makes safe:

  - REPORT_OFFER_KINDS: the whitelist. SCHEDULE changes only. A report offering
    `check_in` or `intent` would be the report telling the runner what they did,
    and `split_block`/`merge_blocks` are the same thing about their session
    shape. Those four stay conversational, where the runner is the one talking.
  - ground_offer: the STORE-time gate. An offer must coerce through the same
    `ProposedActionRequest` the thread's offers coerce through, must name a kind
    on the whitelist, and — the #943 property — must name a planned session the
    PACK ACTUALLY SHOWED. A report cannot offer a change to a session it could
    not see. Every failure DROPS the offer and keeps the report: degrade-not-
    withhold is the tail's existing discipline, and a malformed offer is not
    worth a runner's coaching.
  - mint_report_offer: the READ-time mint. This is the decision the whole module
    turns on. A report is written by a background worker and read by the runner
    hours later over Telegram; the action token lives thirty minutes, so a token
    minted at generation is always dead on arrival. So the offer is stored
    WITHOUT a token, and the token is minted when the owner reads the report —
    scoped to the authenticated reader, re-resolving ownership of everything the
    offer names, exactly as the thread's mint does. Nothing is written until the
    runner confirms, through the one existing confirm endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.coach import CoachMessageReport, CoachReportOffer
from app.schemas.coach_context import CoachContextPack

logger = logging.getLogger(__name__)

# The whitelist, held here rather than only in the tool schema: a model is asked
# nicely by an enum and held to it by this set.
#
# `draft_plan` is EXCLUDED DELIBERATELY, not forgotten. Two reasons, and the
# second is the one that would still hold if the first were fixed:
#
#   1. A report's offer is minted afresh on every read and nothing records that
#      one was acted on, so an offer stays tappable after it has been taken. For
#      the two kinds here that is cosmetic — re-confirming re-completes a
#      completed session or re-applies the same target, and both land on the same
#      value. `draft_plan` is not idempotent: a second confirm drafts AGAIN and
#      supersedes the plan the first one wrote, which is #883 arriving from an
#      evergreen button on a months-old report.
#   2. It is the wrong SIZE of action for this surface. A report is about one
#      run; redrafting the whole block is a conversation, with the runner saying
#      what the next weeks need to look like. The thread still offers it, which
#      is where that discussion already happens.
REPORT_OFFER_KINDS: Set[str] = {"adjust_session", "complete_session"}


# The tail tool's `offer` property. Hand-frozen alongside the rest of the tail
# (the RECORD_COACH_TAIL_TOOL discipline): strict subset, additionalProperties
# false, and `required` naming only what is always present. The semantics live
# here rather than in a prompt clause because that is where the tail's other
# affordances already carry theirs — and because it keeps this change off the
# prompt lineage, so no prompt version flips and no rollback target moves.
OFFER_TOOL_PROPERTY: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action_type"],
    "description": (
        "OPTIONAL. Offer ONE change to the runner's SCHEDULE, for them to confirm "
        "with a tap on this report. Use it only when your message has already "
        "argued for that change — the offer is the message's affordance, never a "
        "substitute for saying why. Offering is not doing: this puts a card under "
        "your report and nothing is written unless the runner taps it, so never "
        "write as though the change has been made. Omit it entirely for a report "
        "that does not conclude the plan should change, which is most of them. "
        "Only ids the pack put in front of you: an id you did not read in "
        "right_now.schedule is dropped."
    ),
    "properties": {
        "action_type": {
            "type": "string",
            "enum": sorted(REPORT_OFFER_KINDS),
            "description": (
                "adjust_session: correct how far or how long ONE planned session "
                "should be. complete_session: mark a planned session done when "
                "this run WAS that session and it was not matched automatically. "
                "Both are corrections to a single session. Redrafting the block "
                "is not offered here — that is a conversation, and you can offer "
                "it there; a report about one run is the wrong place to replace "
                "the plan the runner is training to."
            ),
        },
        "planned_session_id": {
            "type": "string",
            "description": (
                "Required for adjust_session and complete_session: the session_id "
                "of a session in right_now.schedule. Never invent one."
            ),
        },
        "target_distance_m": {
            "type": "number",
            "description": (
                "adjust_session only: what the WHOLE session should be, in metres, "
                "door to door. Give this or target_duration_s, not both."
            ),
        },
        "target_duration_s": {
            "type": "integer",
            "description": (
                "adjust_session only: what the whole session should be, in seconds, "
                "when it is prescribed by time rather than distance."
            ),
        },
    },
}


def coerce_offer(raw: Any) -> Optional[CoachReportOffer]:
    """Shape-only coercion, used where the pack is not in hand (the tail merge).

    Never raises: an off-shape offer is dropped, and the tail it arrived with is
    kept. Popping it out before the tail is constructed is what stops a typo in
    an optional affordance costing the runner their next_steps.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return CoachReportOffer.model_validate(raw)
    except Exception:  # noqa: BLE001 -- an offer is never worth a degraded tail
        logger.warning("coach report offer failed shape validation; dropped")
        return None


def ground_offer(
    report: CoachMessageReport,
    pack: CoachContextPack,
    *,
    is_opener: bool = False,
) -> CoachMessageReport:
    """Drop the report's offer unless it is whitelisted, well-formed and grounded.

    Returns the report unchanged when there is nothing to drop, so the common
    case (no offer) allocates nothing. An opener carries no offer at all: stage
    one is a brief reaction written before the coach has said anything to hang an
    offer on, and the two-stage row would then have to decide which stage's offer
    survives the fuller turn filling it in.
    """
    offer = report.offer
    if offer is None:
        return report
    if is_opener:
        logger.warning("coach opener produced an offer; dropped (fuller-turn only)")
        return report.model_copy(update={"offer": None})

    reason = _reject_reason(offer, pack)
    if reason is None:
        return report
    logger.warning("coach report offer dropped: %s", reason)
    return report.model_copy(update={"offer": None})


def _reject_reason(offer: CoachReportOffer, pack: CoachContextPack) -> Optional[str]:
    """Why this offer must not be stored, or None if it may be."""
    if offer.action_type not in REPORT_OFFER_KINDS:
        return f"action_type {offer.action_type!r} is not offerable from a report"

    # The thread's own contract is the authority on argument shape: exactly one
    # target for adjust_session, a session id where one is needed, no correction
    # arguments riding on an action that does not take them. Re-using it means
    # the report cannot open a looser channel into the same writers than the
    # conversation has.
    from app.services.coach.proposed_actions import ProposedActionRequest

    try:
        ProposedActionRequest.model_validate(
            offer.model_dump(mode="json", exclude_none=True)
        )
    except Exception as exc:  # noqa: BLE001 -- pydantic ValidationError or worse
        return f"off-contract offer ({exc.__class__.__name__})"

    # #943: a report may not offer a change to a session it could not see. The
    # pack is the report's whole world; an id from anywhere else is either an
    # invention or a leak, and both are the same drop.
    #
    # The None branch is currently UNREACHABLE and kept deliberately: both
    # whitelisted kinds require a session id, and the coercion above refuses an
    # offer without one before this line is read. It was reachable while
    # `draft_plan` (which names no resource) was offerable, and would be again if
    # a kind like it were ever whitelisted. Leaving it out would make that a
    # silent hole rather than a covered case.
    if offer.planned_session_id is not None:
        shown = _session_ids_in_pack(pack)
        if str(offer.planned_session_id) not in shown:
            return "planned_session_id was not shown to the coach in this pack"
    return None


def _session_ids_in_pack(pack: CoachContextPack) -> Set[str]:
    """Every planned-session id the pack's schedule section actually carried."""
    schedule = getattr(pack, "schedule", None)
    if schedule is None:
        return set()
    ids: Set[str] = set()
    planned = getattr(schedule, "planned_for_this_activity", None)
    if planned is not None and getattr(planned, "session_id", None):
        ids.add(str(planned.session_id))
    for upcoming in getattr(schedule, "still_to_come_this_week", None) or []:
        session_id = getattr(upcoming, "session_id", None)
        if session_id:
            ids.add(str(session_id))
    return ids


def mint_report_offer(
    db: Session, owner_user_id: UUID, report: Any
) -> Optional[Dict[str, Any]]:
    """Mint the single-use action token for a stored offer, for THIS reader.

    Called from the coach-report read endpoint with the AUTHENTICATED user's id.
    That id is the only authority in play: it scopes the Redis key the token is
    written under and it is re-resolved against every row the offer names, so a
    reader who does not own the session gets no card and a reader who does not
    own the report never reaches here (the route's owned-activity dependency
    404s first, and a cross-tenant id is indistinguishable from a missing one).

    Returns the frame the client renders, or None — a report whose offer cannot
    be minted still renders, exactly as it did before it carried one.
    """
    if not settings.COACH_THREADS_ENABLED:
        # The proposed-action surface's own kill switch (#784). It gates the
        # confirm endpoint, so a card minted through it would be a button that
        # 503s. The switch exists precisely for the coach's self-initiated write
        # path misbehaving, and this is that path on a second surface.
        return None

    offer = getattr(report, "offer", None)
    if offer is None:
        return None
    # Belt and braces over the store-time whitelist: a row written by an older
    # build, or by a future kind added to CoachReportOffer without being added
    # here, must not become a card.
    if offer.action_type not in REPORT_OFFER_KINDS:
        logger.warning(
            "stored report offer names a non-offerable kind %r; not minted",
            offer.action_type,
        )
        return None

    from app.services.coach.proposed_actions import mint_proposed_action

    try:
        _result, frame = mint_proposed_action(
            db, owner_user_id, offer.model_dump(mode="json", exclude_none=True)
        )
    except Exception:  # noqa: BLE001 -- a card is never worth failing the report read
        logger.exception("coach report offer mint failed")
        return None
    return frame
