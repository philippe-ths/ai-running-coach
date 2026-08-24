"""A3 output contract — the prose message + thin structured tail (schema 2.0).

One Anthropic call with adaptive thinking emits, in token order: private
reasoning, the coach's prose `message` (text blocks, the product), then exactly
one strict-schema tool call `record_coach_tail` carrying a thin bookkeeping tail
(headline, machine-readable next_steps, risk-flag declarations, tappable
questions). This module owns:

  - RECORD_COACH_TAIL_TOOL: the hand-frozen strict tool JSON. Hand-frozen (not
    runtime Pydantic generation) so the prompt-cache prefix stays byte-stable and
    the server does not recompile the schema each call. The strict subset forbids
    string maxLength, so the headline length bound lives in the prompt, not here.
  - parse_blocks: order-agnostic extraction of the prose text and the single
    tool_use input from a response's content blocks (SDK objects or plain dicts).
  - merge_report: fold (message, tail) into a CoachMessageReport with the
    degrade-not-withhold rule — a real message survives a missing/unusable tail
    as tail_degraded=True; an empty message is refused (the service then stores a
    templated fallback). A tail is never kept without its message.

The retry / stop_reason discipline (skip-the-tail corrective retry, truncation,
refusal→fallback) lives in the llm method and service, not here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas.coach import CoachMessageReport
from app.services.coach.report_offer import OFFER_TOOL_PROPERTY, coerce_offer

logger = logging.getLogger(__name__)

TOOL_NAME = "record_coach_tail"


class EmptyMessageError(ValueError):
    """Raised when a response carries no prose message. The product is the
    message; without one there is nothing to store, so the service falls back."""


def is_opener_only(report: Any) -> bool:
    """True if a stored report dict is an A4 OPENER-ONLY row: the opener wrote its
    prose into `opener_message` and the fuller turn has not yet filled `message`.

    The single in-band completeness signal (no Activity-sentinel coupling), shared
    by the retrieval seam — which excludes incomplete exchanges from the M4/M7
    prior-exchange reads so an opener never leaks a thin digest or shadows an
    earlier complete exchange — and the M5 eval harness, which scores the fuller
    turn only. A complete message report has a non-empty `message`; a legacy
    structured report has no `opener_message`, so it is never opener-only.
    """
    if not isinstance(report, dict):
        return False
    return bool(report.get("opener_message")) and not (report.get("message") or "").strip()


# Hand-frozen strict tool schema. additionalProperties:false + required on every
# object are the strict-subset requirements; no string maxLength (forbidden in
# the strict subset — the headline bound is a prompt instruction).
RECORD_COACH_TAIL_TOOL: Dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Record the structured bookkeeping tail for the coaching message you just "
        "wrote. The tail carries affordances and memory hooks only — it must not "
        "contain anything the message did not already say. Call this exactly once, "
        "after the prose message."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "next_steps", "risks", "questions"],
        "properties": {
            "headline": {
                "type": "string",
                "description": "A short verdict label for this run (<= 80 chars).",
            },
            "next_steps": {
                "type": "array",
                "description": "0-3 concrete next steps, each restating advice already in the message.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "details", "why"],
                    "properties": {
                        "action": {"type": "string"},
                        "details": {"type": "string"},
                        "why": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["field", "value"],
                                "properties": {
                                    "field": {"type": "string"},
                                    "value": {
                                        "type": ["string", "number", "boolean", "null"]
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "risks": {
                "type": "array",
                "description": "Risk-flag declarations; flag must be an exact name from the metrics flags array.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["flag", "explanation", "mitigation"],
                    "properties": {
                        "flag": {"type": "string"},
                        "explanation": {"type": "string"},
                        "mitigation": {"type": "string"},
                    },
                },
            },
            "questions": {
                "type": "array",
                "description": "0-4 follow-up questions with optional typed tappable options.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question", "reason", "options"],
                    "properties": {
                        "question": {"type": "string"},
                        "reason": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id", "label", "kind"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "kind": {
                                        "type": "string",
                                        "enum": ["rpe", "pain", "reply", "dispute", "custom"],
                                    },
                                    "payload": {
                                        "type": ["string", "number", "boolean", "null"]
                                    },
                                },
                            },
                        },
                    },
                },
            },
            # #944: the optional schedule-change OFFER. Optional (not in
            # `required`) like schedule_fuller_turn below: most reports do not
            # conclude the plan should change, and a required field would invite
            # one every time. Its whitelist, its grounding against the pack and
            # its read-time token all live in services/coach/report_offer.py —
            # this is only where the model is told the field exists.
            "offer": OFFER_TOOL_PROPERTY,
            # A4 two-stage Exchange (opener mode only). Optional, like evidence/
            # payload above (not in `required`): the opener LLM sets it to its depth
            # judgment; the fuller turn omits it. The deterministic safety override
            # forces a fuller turn regardless, so a missing/false value here is never
            # the last word on a red-flag run.
            "schedule_fuller_turn": {
                "type": "boolean",
                "description": (
                    "OPENER MODE ONLY: true if this run warrants a fuller follow-up "
                    "coaching turn (something substantive to say once the runner has "
                    "had a moment); false or omitted for an unremarkable run. Ignored "
                    "in fuller mode."
                ),
            },
        },
    },
}


@dataclass
class ParsedBlocks:
    """The prose message and the single tool tail, extracted from a response."""
    message: str
    tail: Optional[Dict[str, Any]]


def _block_attr(block: Any, key: str) -> Any:
    """Read a field from a content block that may be an SDK object or a dict."""
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def parse_blocks(content_blocks: List[Any]) -> ParsedBlocks:
    """Order-agnostic extraction: join all text blocks into the message, take the
    input of the single `record_coach_tail` tool_use block as the tail.

    Tolerates either Anthropic SDK block objects or plain dicts. Text blocks are
    concatenated in arrival order; a tail is recognised only by the tool name, so
    other tool calls (there should be none) are ignored. If more than one tail
    block appears, the first wins and the rest are logged.
    """
    text_parts: List[str] = []
    tail: Optional[Dict[str, Any]] = None
    for block in content_blocks or []:
        btype = _block_attr(block, "type")
        if btype == "text":
            text = _block_attr(block, "text")
            if text:
                text_parts.append(text)
        elif btype == "tool_use" and _block_attr(block, "name") == TOOL_NAME:
            tool_input = _block_attr(block, "input")
            if tail is None:
                tail = dict(tool_input) if isinstance(tool_input, dict) else None
            else:
                logger.warning("multiple %s tool blocks; ignoring extras", TOOL_NAME)
    return ParsedBlocks(message="\n\n".join(text_parts).strip(), tail=tail)


def merge_report(parsed: ParsedBlocks) -> CoachMessageReport:
    """Fold parsed blocks into a CoachMessageReport.

    Degrade-not-withhold: an empty message is refused (EmptyMessageError → the
    service stores a templated fallback); a real message with a missing or
    unusable tail is kept with tail_degraded=True and an empty next_steps (the
    M7 loop then abstains, the existing discipline). A tail is never kept without
    its message.
    """
    message = parsed.message.strip()
    if not message:
        raise EmptyMessageError("response carried no prose message")

    if parsed.tail is None:
        return CoachMessageReport(message=message, tail_degraded=True)

    # #944: the offer is lifted out BEFORE the tail is constructed and coerced on
    # its own. Left in the spread, a mistyped optional affordance would raise and
    # degrade the whole tail — costing the runner their next_steps over a card.
    # The offer is dropped on its own instead; the tail is unaffected either way.
    #
    # The copy is GUARDED. Before #944 a non-mapping tail reached `**parsed.tail`
    # and raised TypeError inside the try below, which degraded. Copying it out
    # here without this guard moved that failure OUTSIDE the try and changed its
    # type — `dict()` raises ValueError on a sequence of non-pairs — so it escaped
    # `merge_report`, escaped `_generate_message`'s except clause, and cost the
    # runner the whole report. Latent, because `parse_blocks` normalises the tail
    # to a mapping or None; kept honest anyway, since the comment above claims
    # this change makes degradation safer and for that input it did the reverse.
    if not isinstance(parsed.tail, Mapping):
        # Exactly what `**parsed.tail` used to demand, asked explicitly. A
        # try/except around `dict()` is NOT the same restoration: `dict()`
        # happily converts an empty tuple or a sequence of pairs, both of which
        # `**` rejected, so the degrade would silently stop firing for them.
        logger.warning("coach tail was not a mapping; degrading")
        return CoachMessageReport(message=message, tail_degraded=True)
    tail = dict(parsed.tail)
    offer = coerce_offer(tail.pop("offer", None))
    try:
        report = CoachMessageReport(message=message, tail_degraded=False, **tail)
    except (ValidationError, TypeError) as exc:
        # A malformed tail must not discard good coaching — degrade instead.
        logger.warning("coach tail failed validation; degrading: %s", exc)
        return CoachMessageReport(message=message, tail_degraded=True, offer=offer)
    return report.model_copy(update={"offer": offer}) if offer else report


def merge_opener(parsed: ParsedBlocks) -> CoachMessageReport:
    """Fold an OPENER-stage response into a CoachMessageReport (A4 stage one).

    The opener prose lands in `opener_message`; `message` stays empty (the fuller
    turn fills it later, preserving the opener). Only the opener-relevant tail
    fields are carried: `questions` (the RPE/pain prompts) and `schedule_fuller_turn`
    (the LLM's depth judgment), plus an optional `headline`. `next_steps` and
    `risks` are deliberately NOT carried — the opener makes no commitments and
    contributes nothing to the learning loop (which attaches to the fuller turn
    only), so an opener row can never leak a next_step into the M7 adherence read.

    Degrade-not-withhold, mirroring merge_report: an empty opener prose is refused
    (EmptyMessageError → the service stores a templated opener fallback); a missing
    or unusable tail keeps the prose with tail_degraded=True (no RPE/pain prompts,
    schedule_fuller_turn defaults False — the deterministic safety override is then
    the only path to a fuller turn, which is the intended conservative default).
    """
    prose = parsed.message.strip()
    if not prose:
        raise EmptyMessageError("opener carried no prose message")

    if parsed.tail is None:
        return CoachMessageReport(message="", opener_message=prose, tail_degraded=True)

    try:
        return CoachMessageReport(
            message="",
            opener_message=prose,
            tail_degraded=False,
            headline=parsed.tail.get("headline"),
            questions=parsed.tail.get("questions", []),
            schedule_fuller_turn=bool(parsed.tail.get("schedule_fuller_turn", False)),
        )
    except (ValidationError, TypeError) as exc:
        logger.warning("opener tail failed validation; degrading: %s", exc)
        return CoachMessageReport(message="", opener_message=prose, tail_degraded=True)
