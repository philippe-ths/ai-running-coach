"""
Coach report generation: the LLM call paths for both prompt families (the legacy
structured JSON report and the prose message), their retries, policy gate,
fallbacks, and the voice rewrite pass. Split from `service.py` (#1159); `service`
re-exports every name here, and its entry points call these through its namespace.
"""

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import List, Optional

import anthropic
from pydantic import ValidationError

from app.schemas.coach import CoachMessageReport, CoachReportContent
from app.schemas.coach_context import CoachContextPack
from app.services.coach.output_contract import (
    RECORD_COACH_TAIL_TOOL,
    EmptyMessageError,
    merge_opener,
    merge_report,
    parse_blocks,
)
from app.services.coach.report_offer import ground_offer
from app.services.coach.turn import CoachClient, over_budget as budget_over
from app.services.coach.validator import (
    PolicyViolation,
    validate_message_policy,
    validate_policy,
)
from app.services.coach.voice_rewrite import revoice_report

logger = logging.getLogger(__name__)

# Rule 5, the medical-scope floor. Named once because the voice rewrite must never
# treat it as "already broken by the baseline" (#1142).
MEDICAL_OVERREACH_RULE = "medical_overreach"

# Token budget for the A3 message call (thinking tokens count against it).
_MESSAGE_MAX_TOKENS = 8192

# #295: the ESCALATED budget used to RETRY a message call that truncated at
# max_tokens (or returned no prose). A multi-activity block produces a richer pack,
# so the model reasons longer under adaptive thinking and can spend the whole
# budget on thinking before emitting the prose — retrying at the SAME budget hits
# the identical wall and degrades to a canned fallback (the live #295 failure).
# Retrying with more room lets the prose land. Generous headroom over the worst
# observed thinking spend; only paid on the rare truncation retry.
_MESSAGE_MAX_TOKENS_ESCALATED = 16384

# Token budget for the A4 OPENER call. The opener is a brief reaction + a small
# tail, but adaptive-thinking tokens count against max_tokens too, so this leaves
# headroom for the private reasoning while keeping the opener materially lighter
# than the fuller turn. Tunable.
_OPENER_MAX_TOKENS = 2048

# #295: the escalated retry budget for the lean opener (kept proportionally smaller
# than the fuller's, since the opener is meant to be brief — a heavy thinking spend
# on an opener is itself suspect, but the extra room still beats a fallback).
_OPENER_MAX_TOKENS_ESCALATED = 4096

# #217: total attempts at the prose call before degrading to a fallback. The prose
# response is occasionally empty (no text block) but recovers on an immediate
# re-run, so one retry (2 attempts) reclaims that transient failure. Both stages
# (opener and fuller) share it.
_EMPTY_PROSE_ATTEMPTS = 2

# Token budget for the legacy structured JSON report. Was 1024, which truncated
# under claude-sonnet-4-6 (more verbose JSON than the retired model) — the report
# hit stop_reason=max_tokens mid-object and the partial/fenced text failed to
# parse, silently falling back. The structured report is bounded (≤6 takeaways,
# ≤3 next_steps, evidence arrays), so 4096 gives ample headroom without truncation.
_STRUCTURED_MAX_TOKENS = 4096

# Nudge appended when the model wrote prose but skipped the tail tool.
_TAIL_REMINDER = (
    "You wrote the message but did not call record_coach_tail. Call it now, "
    "exactly once, restating only what your message already said."
)

# Templated fallback prose when no usable message could be produced.
_FALLBACK_MESSAGE = (
    "I couldn't put together a full write-up for this run just now. Your metrics "
    "are recorded — take a look at the activity detail for the flags, splits and "
    "zones, and I'll pick this up properly on your next run."
)

# Templated fallback prose for the A4 opener (LLM/parse/transport failure): a
# brief, non-committal reaction that keeps the exchange alive (the fuller turn can
# still follow). Lands in opener_message, message stays empty.
_FALLBACK_OPENER_MESSAGE = (
    "Nice work getting that run in. I'll take a proper look and follow up shortly."
)


@dataclass
class _GenOutcome:
    """The normalised result of either generation path, so storage is shared.

    `report_dump` is the dict stored in CoachReport.report (a CoachReportContent
    for the structured family, a CoachMessageReport for the prose family).
    """
    report_dump: dict
    raw_response: str
    is_fallback: bool
    policy_violations: List[str] = field(default_factory=list)
    tail_degraded: bool = False
    # #822/#824: what the voice rewrite did, or why the baseline stands. None on the
    # structured family and on a fallback, where no rewrite is attempted at all.
    voice_rewrite: Optional[str] = None


# --- structured family (legacy CoachReportContent, schema 1.2) ----------------


def _structured_fallback_dump() -> dict:
    """The templated structured fallback report (LLM/parse/transport failure)."""
    return CoachReportContent(
        key_takeaways=[
            {"text": "Analysis is temporarily unavailable for this activity."},
            {"text": "Your metrics have been recorded and can be reviewed in the detail view."},
        ],
        next_steps=[
            {
                "action": "Review your metrics manually",
                "details": "Check the activity detail page for flags and zones.",
                "why": "The AI coaching summary could not be generated for this session.",
            }
        ],
    ).model_dump()


async def _generate_structured(
    client: CoachClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
    *,
    user_id=None,
) -> _GenOutcome:
    """The legacy structured path: constrained-JSON generation, policy gate, one
    corrective retry, templated fallback on failure. Behaviour preserved from the
    prior inline implementation."""
    # P2.2: at the per-user/global spend cap, degrade to the deterministic
    # fallback BEFORE spending any tokens (the cap is observed before the call).
    if user_id is not None and budget_over(user_id):
        logger.info("coach_budget_degraded_structured", extra={"user_id": str(user_id)})
        return _GenOutcome(
            report_dump=_structured_fallback_dump(), raw_response="", is_fallback=True,
        )
    raw_response = ""
    policy_violations: List[str] = []
    try:
        raw_response = await client.generate_json(
            system=system_prompt, user=user_message, max_tokens=_STRUCTURED_MAX_TOKENS,
        )
        cleaned = _strip_code_fences(raw_response)
        parsed = json.loads(cleaned)
        content = CoachReportContent.model_validate(parsed)

        violations = validate_policy(content, pack)
        if violations:
            logger.info(
                "Policy violations detected: %s — attempting retry",
                [v.rule for v in violations],
            )
            content, retry_violations = await _retry_with_fixes(
                client, system_prompt, user_message, pack, violations
            )
            if retry_violations:
                logger.warning(
                    "Policy violations persisted after retry: %s",
                    [v.rule for v in retry_violations],
                )
                policy_violations = [v.rule for v in retry_violations]
    except (json.JSONDecodeError, ValidationError, anthropic.APIError) as e:
        logger.error("Coach report parse/validation/transport error: %s", e)
        return _GenOutcome(
            report_dump=_structured_fallback_dump(),
            raw_response=raw_response, is_fallback=True,
        )

    return _GenOutcome(
        report_dump=content.model_dump(),
        raw_response=raw_response,
        is_fallback=False,
        policy_violations=policy_violations,
    )


# --- prose-message family (A3 CoachMessageReport, schema 2.0) ------------------


def _message_fallback_outcome() -> _GenOutcome:
    """A fallback that keeps the runner inside the prose family: a templated
    message and a degraded tail. A tail is never stored without its message."""
    return _GenOutcome(
        report_dump=CoachMessageReport(
            message=_FALLBACK_MESSAGE, tail_degraded=True
        ).model_dump(),
        raw_response="",
        is_fallback=True,
        tail_degraded=True,
    )


def _serialize_blocks(content_blocks: list) -> str:
    """A readable serialisation of the response blocks for the debug column: the
    prose message followed by the tail JSON. Best-effort; never raises."""
    try:
        parsed = parse_blocks(content_blocks)
        out = parsed.message
        if parsed.tail is not None:
            out += "\n\n[tail]\n" + json.dumps(parsed.tail, default=str, indent=2)
        return out
    except Exception:  # noqa: BLE001 — debug serialisation must never break storage
        return ""


async def _call_message(
    client,
    system_prompt: str,
    user_message: str,
    *,
    max_tokens: int = _MESSAGE_MAX_TOKENS,
    user_id=None,
):
    """One prose-message sub-call.

    Spend is NOT recorded here any more: the client this turn was handed is a
    `turn.MeteredClient`, which records every sub-call including the truncation,
    tail-skip and policy-fix fan-out (#801). The recording point is identical,
    it is simply no longer a thing each call site has to remember. `user_id` is
    kept on the signature because the caller still uses it for the entry gate.
    """
    return await client.generate_coach_message(
        system=system_prompt,
        user=user_message,
        tools=[RECORD_COACH_TAIL_TOOL],
        max_tokens=max_tokens,
    )


async def _reattempt_if_truncated(
    client: CoachClient,
    system_prompt: str,
    user_message: str,
    result,
    *,
    escalated: int,
    context: str,
    user_id=None,
):
    """If a prose-message call stopped on the token ceiling, re-attempt it ONCE at
    a larger budget and return whichever result is better (#295/#282).

    The same single re-attempt the first generation phase already does, factored out
    so the policy-fix RETRY can reuse it identically (#282): the corrective retry
    re-sends the full original context PLUS the fix instructions, so it is a longer
    prompt than the first call and truncates more readily — without this, a complete
    first attempt is preferred over a truncated retry (#274) and the fix never lands.

    Truncation is detected uniformly by `stop_reason == "max_tokens"`. The extra
    call happens ONLY on a truncated result (one re-attempt, at most). The retried
    result replaces the original unless it refuses (a refusal is worse than a
    truncated turn — the caller still has prose to fall back on). A turn that is
    STILL truncated after the escalation is surfaced loudly rather than silently
    shipped half-written. `context` tags the log line (first / policy retry)."""
    if result.stop_reason != "max_tokens":
        return result
    logger.info(
        "coach message truncated (max_tokens) on %s; retrying at a larger budget (#295/#282)",
        context,
    )
    retried = await _call_message(
        client, system_prompt, user_message, max_tokens=escalated, user_id=user_id
    )
    if retried.stop_reason != "refusal":
        result = retried
    if result.stop_reason == "max_tokens":
        logger.warning(
            "coach message STILL truncated after budget escalation on %s (#295/#282); "
            "the turn may degrade — investigate the pack size / thinking spend",
            context,
        )
    return result


def _opener_fallback_outcome() -> "_GenOutcome":
    """A4 opener fallback: a templated brief reaction in opener_message (message
    empty), a degraded tail, no schedule judgment. The deterministic safety
    override still drives scheduling on a red-flag run."""
    return _GenOutcome(
        report_dump=CoachMessageReport(
            message="", opener_message=_FALLBACK_OPENER_MESSAGE, tail_degraded=True,
        ).model_dump(),
        raw_response="",
        is_fallback=True,
        tail_degraded=True,
    )


def _fix_instructions(violations: List[PolicyViolation]) -> str:
    """The corrective retry's instruction list, one line per violation.

    Shared by both retry paths (#801). Only the JOIN is shared: the two
    preambles around it differ in wording ("Your previous MESSAGE ... Rewrite the
    message and the tail" vs "Your previous RESPONSE ... Fix these issues"), and
    those are bytes that reach the model, so unifying them is a prompt change
    that belongs to the prompt phase, not to this refactor.
    """
    return "\n".join(f"- {v.rule}: {v.fix_instruction}" for v in violations)


@dataclass
class _MsgAttempt:
    """One prose-message generation attempt, carrying the signals storage needs to
    pick the best of several (#274): its report, the raw serialization that produced
    it (so raw_llm_response always matches the STORED report, never a discarded
    attempt), whether the model ran out of tokens (a truncated, half-written message),
    and its surviving policy violations."""
    report: CoachMessageReport
    raw_response: str
    truncated: bool
    violations: List[PolicyViolation]


def _choose_message_attempt(first: _MsgAttempt, retry: _MsgAttempt) -> _MsgAttempt:
    """Pick the better of two attempts to store (#274). A complete (non-truncated)
    attempt beats a truncated one — a runner is better served by a complete message
    that trips a tolerated non-medical rule than by a half-sentence. Among
    equally-(non-)truncated attempts, fewer surviving violations wins, and a tie goes
    to the corrective `retry`. The medical-overreach-forces-fallback rule is applied
    by the caller to whichever attempt this returns, so the safety floor is unaffected."""
    if first.truncated != retry.truncated:
        return retry if first.truncated else first
    return retry if len(retry.violations) <= len(first.violations) else first


async def _generate_message(
    client: CoachClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
    *,
    is_opener: bool = False,
    user_id=None,
    voice=None,
) -> _GenOutcome:
    """The prose-message path: one adaptive-thinking call producing prose + a tool
    tail, with stop_reason-aware retries, the policy gate over message+tail, and
    degrade-not-withhold semantics (ADR 0009).

    `is_opener` selects the A4 opener variant: a shorter max_tokens, the
    opener merge (prose -> opener_message, message empty), and the opener fallback.
    The stop_reason / retry / policy / medical-overreach-forces-fallback discipline
    is identical for both stages, so the opener is policed exactly as the fuller
    turn (AC3).

    stop_reason handling: `refusal` -> templated fallback; `max_tokens` -> one
    re-attempt before degrading to whatever arrived; `end_turn` with no tail ->
    one corrective retry asking for the tool, then degrade. A surviving
    medical-overreach violation forces a fallback (the one strengthening over the
    structured path), since prose renders verbatim.
    """
    max_tokens = _OPENER_MAX_TOKENS if is_opener else _MESSAGE_MAX_TOKENS
    escalated = _OPENER_MAX_TOKENS_ESCALATED if is_opener else _MESSAGE_MAX_TOKENS_ESCALATED
    merge = merge_opener if is_opener else merge_report
    fallback = _opener_fallback_outcome if is_opener else _message_fallback_outcome
    # P2.2: at the spend cap, degrade to the deterministic fallback BEFORE the
    # call (cap observed before tokens are spent; one user's cap never affects
    # another since the counter is keyed by activity-owner user_id).
    if user_id is not None and budget_over(user_id):
        logger.info("coach_budget_degraded_message", extra={"user_id": str(user_id)})
        return fallback()
    # #217: the prose call occasionally returns no text block at all (an empty-prose
    # response, observed as transient — an immediate re-run recovers it). A single
    # in-process retry turns that one hiccup into a real turn instead of a fallback,
    # which matters most for the safety-forced fuller turn whose substantive coaching
    # would otherwise be silently dropped.
    # #295: a truncation (or empty-prose) retry escalates the TOKEN BUDGET rather
    # than re-running at the same one that just truncated — a rich multi-activity
    # pack can spend the whole budget on thinking before any prose, so the same
    # budget fails identically and degrades to a canned fallback. More room lets the
    # prose land. The empty-prose retry (attempt 1) uses the escalated budget too.
    report = None
    raw_response = ""
    truncated = False
    for attempt in range(_EMPTY_PROSE_ATTEMPTS):
        budget = max_tokens if attempt == 0 else escalated
        try:
            result = await _call_message(
                client, system_prompt, user_message, max_tokens=budget, user_id=user_id
            )
            if result.stop_reason == "refusal":
                logger.warning("coach message refused; storing fallback")
                return fallback()
            # #295: a truncated first attempt re-attempts ONCE at a larger budget.
            # Shared with the policy-fix retry's truncation re-attempt (#282).
            result = await _reattempt_if_truncated(
                client, system_prompt, user_message, result,
                escalated=escalated, context="first attempt", user_id=user_id,
            )

            parsed = parse_blocks(result.content_blocks)
            # Tail-skip corrective retry: the model is allowed to skip the tool under
            # tool_choice=auto. One nudge before accepting a degraded tail.
            if parsed.tail is None and result.stop_reason == "end_turn":
                logger.info("coach message skipped the tail tool; one corrective retry")
                nudge = f"{user_message}\n\n{_TAIL_REMINDER}"
                result2 = await _call_message(
                    client, system_prompt, nudge, max_tokens=budget, user_id=user_id
                )
                parsed2 = parse_blocks(result2.content_blocks)
                if parsed2.tail is not None and parsed2.message.strip():
                    parsed, result = parsed2, result2

            raw_response = _serialize_blocks(result.content_blocks)
            truncated = result.stop_reason == "max_tokens"  # #274: a half-written msg
            report = merge(parsed)
            break
        except EmptyMessageError:
            if attempt + 1 < _EMPTY_PROSE_ATTEMPTS:
                logger.warning(
                    "coach response carried no prose message; retrying at a larger budget (#217/#295)"
                )
                continue
            logger.warning(
                "coach response carried no prose message after retry; storing fallback"
            )
            return fallback()
        except anthropic.APIError as e:
            logger.error("coach message transport error: %s", e)
            return fallback()

    # #274: track the first attempt with the raw serialization that produced it, so
    # whichever attempt we store carries ITS OWN raw_llm_response (never a discarded
    # one). The policy retry is judged against it rather than blindly replacing it.
    chosen = _MsgAttempt(
        report=report,
        raw_response=raw_response,
        truncated=truncated,
        violations=validate_message_policy(report, pack),
    )
    if chosen.violations:
        logger.info(
            "Message policy violations detected: %s — attempting retry",
            [v.rule for v in chosen.violations],
        )
        retry_attempt = await _retry_message_with_fixes(
            client, system_prompt, user_message, pack, chosen.violations,
            is_opener=is_opener, user_id=user_id,
        )
        if retry_attempt is not None:
            # #274: store the BETTER attempt — a complete attempt is not clobbered by
            # a truncated retry, and raw_response follows the report we keep.
            chosen = _choose_message_attempt(chosen, retry_attempt)

    if any(v.rule == "medical_overreach" for v in chosen.violations):
        # ADR 0009: medical overreach in the attempt we would store forces a fallback,
        # because the prose renders verbatim to the runner. Use the STAGE-CORRECT
        # fallback (the variable bound above), so an opener overreach yields an
        # opener-shaped fallback (message empty, opener_message set) that stays
        # opener-only — otherwise the safety-forced fuller turn would cache-hit a
        # non-opener-only fallback and never regenerate, silently defeating the
        # safety floor on exactly the red-flag runs that trip rule 5.
        logger.warning("medical overreach in the stored attempt; forcing fallback")
        return fallback()
    if chosen.violations:
        logger.warning(
            "Message policy violations persisted: %s",
            [v.rule for v in chosen.violations],
        )

    # #944: the offer is settled here, once, on the attempt that will actually be
    # stored — after the policy retry has chosen between attempts, so a retry that
    # named a different session is judged on its own offer, and before the voice
    # rewrite, which re-words prose and must never be handed an offer that was
    # about to be dropped. Grounding never raises and never withholds the report:
    # an ungrounded, off-contract or non-whitelisted offer is simply not stored.
    chosen = replace(
        chosen, report=ground_offer(chosen.report, pack, is_opener=is_opener)
    )

    # #822: the prose above was written with no voice input at all. It is the
    # baseline, and it stays where every downstream reader looks for it. The
    # runner's voice is applied here, as a rewrite that can re-word and
    # re-emphasise but cannot reach the facts — which is the whole reason the two
    # passes are separate.
    chosen, voice_rewrite = await _apply_voice(
        chosen, pack, voice=voice, user_id=user_id, is_opener=is_opener
    )

    return _GenOutcome(
        report_dump=chosen.report.model_dump(),
        raw_response=chosen.raw_response,
        is_fallback=False,
        policy_violations=[v.rule for v in chosen.violations],
        tail_degraded=chosen.report.tail_degraded,
        voice_rewrite=voice_rewrite,
    )


async def _apply_voice(
    chosen: _MsgAttempt, pack: CoachContextPack, *, voice, user_id, is_opener: bool
) -> tuple[_MsgAttempt, Optional[str]]:
    """Attach the runner's voiced rendering of this stage's prose, when they have one.

    Both stages are covered, because both are prose the runner reads: the opener
    re-voices `opener_message`, the fuller turn `message`. The baseline is never
    replaced — the voiceless text stays where the digest, the eval harness and the
    learning loop read it, so those go on consuming substance while the runner
    reads style. A rewrite that fails for any reason leaves the report as it was.
    """
    if voice is None:
        return chosen, None

    field = "opener_message" if is_opener else "message"
    voiced_field = "voiced_opener_message" if is_opener else "voiced_message"
    baseline = getattr(chosen.report, field) or ""

    # Judge the rewrite against the baseline's OWN surviving violations, not against
    # zero. A baseline can ship with a soft violation the retry did not clear (the
    # "violations persisted" path), and rejecting the rewrite for inheriting one
    # would discard a good re-voicing for a fault it did not commit. Only a rule the
    # baseline did not already trip is the rewrite's doing.
    # The medical floor is never inherited: a rewrite that names a dose or a
    # diagnosis is refused whatever its baseline did (#1142).
    inherited = {v.rule for v in chosen.violations} - {MEDICAL_OVERREACH_RULE}

    def _introduced(text: str) -> list:
        probe = chosen.report.model_copy(update={field: text})
        return [v for v in validate_message_policy(probe, pack) if v.rule not in inherited]

    outcome = await revoice_report(
        baseline=baseline,
        voice=voice,
        user_id=user_id,
        validate=_introduced,
        is_opener=is_opener,
    )
    # The wall-clock cost rides the stored reason when a call was actually made,
    # so "what does a rewrite cost?" is answerable from the reports themselves.
    stored = outcome.reason
    if outcome.duration_ms is not None:
        stored = f"{stored} in {outcome.duration_ms} ms"
    if not outcome.text:
        return chosen, stored
    return (
        replace(
            chosen,
            report=chosen.report.model_copy(update={voiced_field: outcome.text}),
        ),
        stored,
    )


async def _retry_message_with_fixes(
    client: CoachClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
    *,
    is_opener: bool = False,
    user_id=None,
) -> Optional[_MsgAttempt]:
    """Re-prompt once with fix instructions for the message-policy violations.
    Returns the retry as a _MsgAttempt (#274), or None when the retry produced
    nothing usable (refusal / empty prose / transport error) — the caller then keeps
    the original attempt. `is_opener` keeps the opener's merge + token budget on the
    retry too.

    #282: the corrective retry re-sends the full original context PLUS the fix
    instructions, so it is a longer prompt than the first call and truncates more
    readily. When this retry itself stops on the token ceiling, re-attempt it ONCE at
    the escalated budget before its result is judged — mirroring the first-generation
    phase's truncation re-attempt — so the corrective attempt is less likely to come
    back truncated and discarded by the #274 prefer-complete-over-truncated rule. The
    extra call happens ONLY on a truncated retry."""
    max_tokens = _OPENER_MAX_TOKENS if is_opener else _MESSAGE_MAX_TOKENS
    escalated = _OPENER_MAX_TOKENS_ESCALATED if is_opener else _MESSAGE_MAX_TOKENS_ESCALATED
    merge = merge_opener if is_opener else merge_report
    fix_instructions = _fix_instructions(violations)
    retry_message = (
        "Your previous message had policy violations. Rewrite the message and the "
        "tail to fix these issues ONLY (keep everything else the same):\n"
        f"{fix_instructions}\n\nOriginal context:\n{original_user_message}"
    )
    try:
        result = await _call_message(
            client, system_prompt, retry_message, max_tokens=max_tokens, user_id=user_id
        )
        if result.stop_reason == "refusal":
            return None
        # #282: a truncated policy-fix retry re-attempts ONCE at a larger budget
        # before being judged, exactly as the first generation phase does.
        # _reattempt_if_truncated keeps the original (truncated) result when the
        # re-attempt refuses, so the refusal check above still covers that case.
        result = await _reattempt_if_truncated(
            client, system_prompt, retry_message, result,
            escalated=escalated, context="policy retry", user_id=user_id,
        )
        report = merge(parse_blocks(result.content_blocks))
    except (EmptyMessageError, anthropic.APIError) as e:
        logger.error("Coach message retry error: %s", e)
        return None
    return _MsgAttempt(
        report=report,
        raw_response=_serialize_blocks(result.content_blocks),
        truncated=result.stop_reason == "max_tokens",
        violations=validate_message_policy(report, pack),
    )


async def _retry_with_fixes(
    client: CoachClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
) -> tuple[CoachReportContent, List[PolicyViolation]]:
    """
    Re-prompt the LLM once with fix instructions for policy violations.
    Returns (content, remaining_violations). Never loops more than once.
    """
    fix_instructions = _fix_instructions(violations)
    retry_message = (
        f"Your previous response had policy violations. Fix these issues ONLY "
        f"(keep everything else the same):\n{fix_instructions}\n\n"
        f"Original context:\n{original_user_message}"
    )

    try:
        raw = await client.generate_json(
            system=system_prompt,
            user=retry_message,
            max_tokens=_STRUCTURED_MAX_TOKENS,
        )
        cleaned = _strip_code_fences(raw)
        parsed = json.loads(cleaned)
        content = CoachReportContent.model_validate(parsed)

        # Re-validate — but don't loop again
        remaining = validate_policy(content, pack)
        return content, remaining

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Coach report retry parse error: %s", e)
        raise


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) that LLMs sometimes add.

    Tolerates an UNTERMINATED opening fence: a closing-fence-less or truncated
    response (e.g. ```json\\n{...) would otherwise be handed to json.loads with a
    leading backtick and fail as "Expecting value: line 1 column 1". Stripping a
    leading fence even without its close lets the JSON body parse. (Defence in
    depth alongside `_STRUCTURED_MAX_TOKENS`, which prevents the truncation that
    drops the closing fence in the first place.)"""
    stripped = text.strip()
    # Match a fully fenced block ```json\n...\n``` or ```\n...\n```.
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Otherwise strip a leading opening fence and any trailing fence independently.
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()
