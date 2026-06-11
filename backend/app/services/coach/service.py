"""
Coach service — orchestrates context pack → LLM → validate → policy check → store.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import anthropic

logger = logging.getLogger(__name__)

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.schemas.coach import (
    CoachMessageReport,
    CoachReportContent,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
)
from app.schemas.coach_context import CoachContextPack
from app.services.coach.belief_store import write_back_beliefs
from app.services.coach.consolidation import enqueue_consolidation
from app.services.coach.context import build_context_pack
from app.services.coach.digest import build_report_digest
from app.services.coach.llm import AnthropicClient
from app.services.coach.output_contract import (
    RECORD_COACH_TAIL_TOOL,
    EmptyMessageError,
    merge_report,
    parse_blocks,
)
from app.services.analysis.classifier import Classification, playbook_key
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    build_system_prompt,
)
from app.services.coach.validator import (
    PolicyViolation,
    validate_message_policy,
    validate_policy,
)

# Schema version is keyed by prompt FAMILY (ADR 0009): the legacy structured
# CoachReportContent output is schema 1.2; the A3 prose-message CoachMessageReport
# output is schema 2.0. The active version is resolved from the active prompt id's
# prefix, so a config flip of COACH_PROMPT_ID flips both the prompt and the cache
# identity together. `SCHEMA_VERSION` is retained as the legacy-family default for
# callers that import it.
SCHEMA_VERSION = "1.2"
SCHEMA_VERSION_BY_FAMILY = {
    "coach_report": "1.2",
    "coach_message": "2.0",
}

# Token budget for the A3 message call (thinking tokens count against it).
_MESSAGE_MAX_TOKENS = 8192

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


def active_schema_version(prompt_id: str) -> str:
    """The schema version for a prompt id, resolved by family prefix.

    Unknown prefixes fall back to the legacy structured family (1.2) so an
    unrecognised prompt id can never silently mint a new schema.
    """
    for family, version in SCHEMA_VERSION_BY_FAMILY.items():
        if prompt_id.startswith(family):
            return version
    return SCHEMA_VERSION


def get_active_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Return the cached report row for the *active* version of this activity.

    The active version is the current (COACH_PROMPT_ID, SCHEMA_VERSION) pair.
    Rows from older prompt/schema versions are retained but ignored here, so a
    version bump causes a clean cache miss and regeneration rather than serving
    a stale shape.
    """
    activity_uuid = _coerce_uuid(activity_id)
    prompt_id = settings.COACH_PROMPT_ID
    return (
        db.query(CoachReport)
        .filter(
            CoachReport.activity_id == activity_uuid,
            CoachReport.prompt_id == prompt_id,
            CoachReport.schema_version == active_schema_version(prompt_id),
        )
        .first()
    )


async def get_or_generate_coach_report(
    db: Session, activity_id: str, force: bool = False
) -> Optional[CoachReportRead]:
    """
    Returns the cached report for the active version if one exists, otherwise
    generates a new one via LLM.

    force=True regenerates the active-version report (replacing only that row);
    prior-version reports are always retained, so regeneration is never
    destructive to history.
    """
    activity_uuid = _coerce_uuid(activity_id)

    # Check cache (active version only)
    existing = get_active_report_row(db, activity_uuid)
    if existing and not force:
        return _to_read(existing)
    if force and existing:
        # Replace only the active-version row; prior versions are untouched.
        db.delete(existing)
        db.commit()

    # Load activity
    activity = db.query(Activity).filter(Activity.id == activity_uuid).first()
    if not activity or not activity.metrics:
        return None

    # Build context pack
    pack = build_context_pack(db, activity)
    input_hash = pack.fingerprint()
    pack_dict = pack.to_serializable_dict()

    # Build prompt with activity-type playbook, selected from the axes (ADR 0007)
    prompt_id = settings.COACH_PROMPT_ID
    schema_version = active_schema_version(prompt_id)
    classification = Classification.from_metrics(activity.metrics)
    system_prompt = build_system_prompt(prompt_id, playbook_key(activity, classification))
    user_message = json.dumps(pack_dict, default=str)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    # Dispatch on prompt family (ADR 0009): the A3 prose-message path vs the
    # legacy structured path. Both normalise to a _GenOutcome so storage is shared.
    if prompt_id.startswith(MESSAGE_PROMPT_PREFIX):
        outcome = await _generate_message(client, system_prompt, user_message, pack)
    else:
        outcome = await _generate_structured(client, system_prompt, user_message, pack)

    # Monitoring (A3): one greppable WARNING per stored degraded tail — a real prose
    # message produced but no usable structured tail (the loop abstains on it). This
    # is the single chokepoint covering every degrade path, so it surfaces the
    # `tail_degraded` rate in prod logs / Sentry without a batch job; the eval
    # scorecard's tail_degraded counter is the complementary batch view.
    if outcome.tail_degraded:
        logger.warning(
            "coach_tail_degraded: stored a prose message with a degraded tail "
            "(activity=%s, prompt=%s); the learning loop abstains on this report",
            activity_uuid,
            prompt_id,
        )

    meta = CoachReportMeta(
        confidence=pack.metrics.confidence,
        model_id=settings.COACH_MODEL_ID,
        prompt_id=prompt_id,
        schema_version=schema_version,
        input_hash=input_hash,
        generated_at=datetime.now(timezone.utc),
        policy_violations=outcome.policy_violations,
        tail_degraded=outcome.tail_degraded,
    )

    # A2a: persist the exchange digest alongside the report so later exchanges
    # retrieve it instead of re-projecting from the full report JSON. Skipped for
    # fallbacks (the M4 longitudinal read excludes them anyway). Guarded so a
    # digest hiccup never blocks report storage — the digest is a derived
    # convenience, the report is the record. build_report_digest handles both the
    # structured and the prose-message shapes (ADR 0009).
    report_digest = None
    if not outcome.is_fallback:
        try:
            report_digest = build_report_digest(
                outcome.report_dump, activity.start_date
            ).model_dump()
        except Exception:  # noqa: BLE001 — digest is a derived convenience
            logger.exception(
                "exchange digest projection failed for activity %s", activity_uuid
            )

    # Store (version columns mirror meta so the cache can key on them)
    db_report = CoachReport(
        activity_id=activity_uuid,
        prompt_id=prompt_id,
        schema_version=schema_version,
        report=outcome.report_dump,
        meta=meta.model_dump(mode="json"),
        context_pack=pack_dict,
        raw_llm_response=outcome.raw_response,
        is_fallback=outcome.is_fallback,
        digest=report_digest,
    )
    db.add(db_report)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request generated the active-version row first (the delete
        # -> LLM -> insert window is not atomic). Yield to the row that landed.
        db.rollback()
        winner = get_active_report_row(db, activity_uuid)
        if winner is not None:
            return _to_read(winner)
        raise
    db.refresh(db_report)

    # M8 belief write-back: a successful report feeds the durable belief store the
    # next report reads. Skipped for fallbacks (no real analysis to learn from);
    # best-effort inside, so it never breaks report generation.
    if not outcome.is_fallback:
        write_back_beliefs(db, activity, pack)
        # A2c: re-ground the durable-memory narrative in the background. Enqueued
        # (never awaited) at this same exchange boundary so the user-facing report
        # has already returned by the time the Haiku consolidation runs — the turn
        # never blocks on it. No sentinel needed: consolidation is an idempotent
        # rewrite of the single per-user narrative row, so a force-regeneration
        # re-enqueue is harmless (unlike the belief double-count the sentinel above
        # guards). Best-effort enqueue; a Redis hiccup never breaks report storage.
        enqueue_consolidation(activity.user_id)

    return _to_read(db_report)


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
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
) -> _GenOutcome:
    """The legacy structured path: constrained-JSON generation, policy gate, one
    corrective retry, templated fallback on failure. Behaviour preserved from the
    prior inline implementation."""
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


async def _call_message(client: AnthropicClient, system_prompt: str, user_message: str):
    return await client.generate_coach_message(
        system=system_prompt,
        user=user_message,
        tools=[RECORD_COACH_TAIL_TOOL],
        max_tokens=_MESSAGE_MAX_TOKENS,
    )


async def _generate_message(
    client: AnthropicClient,
    system_prompt: str,
    user_message: str,
    pack: CoachContextPack,
) -> _GenOutcome:
    """The A3 prose-message path: one adaptive-thinking call producing prose + a
    tool tail, with stop_reason-aware retries, the policy gate over message+tail,
    and degrade-not-withhold semantics (ADR 0009).

    stop_reason handling: `refusal` -> templated fallback; `max_tokens` -> one
    re-attempt before degrading to whatever arrived; `end_turn` with no tail ->
    one corrective retry asking for the tool, then degrade. A surviving
    medical-overreach violation forces a fallback (the one strengthening over the
    structured path), since prose renders verbatim.
    """
    try:
        result = await _call_message(client, system_prompt, user_message)
        if result.stop_reason == "refusal":
            logger.warning("coach message refused; storing fallback")
            return _message_fallback_outcome()
        if result.stop_reason == "max_tokens":
            logger.info("coach message truncated (max_tokens); retrying once")
            retried = await _call_message(client, system_prompt, user_message)
            if retried.stop_reason != "refusal":
                result = retried

        parsed = parse_blocks(result.content_blocks)
        # Tail-skip corrective retry: the model is allowed to skip the tool under
        # tool_choice=auto. One nudge before accepting a degraded tail.
        if parsed.tail is None and result.stop_reason == "end_turn":
            logger.info("coach message skipped the tail tool; one corrective retry")
            nudge = f"{user_message}\n\n{_TAIL_REMINDER}"
            result2 = await _call_message(client, system_prompt, nudge)
            parsed2 = parse_blocks(result2.content_blocks)
            if parsed2.tail is not None and parsed2.message.strip():
                parsed, result = parsed2, result2

        raw_response = _serialize_blocks(result.content_blocks)
        report = merge_report(parsed)
    except EmptyMessageError:
        logger.warning("coach response carried no prose message; storing fallback")
        return _message_fallback_outcome()
    except anthropic.APIError as e:
        logger.error("coach message transport error: %s", e)
        return _message_fallback_outcome()

    violations = validate_message_policy(report, pack)
    if violations:
        logger.info(
            "Message policy violations detected: %s — attempting retry",
            [v.rule for v in violations],
        )
        report, remaining = await _retry_message_with_fixes(
            client, system_prompt, user_message, pack, violations, report
        )
        if remaining:
            # ADR 0009: medical overreach surviving the retry forces a fallback,
            # because the prose renders verbatim to the runner.
            if any(v.rule == "medical_overreach" for v in remaining):
                logger.warning("medical overreach survived retry; forcing fallback")
                return _message_fallback_outcome()
            logger.warning(
                "Message policy violations persisted after retry: %s",
                [v.rule for v in remaining],
            )
            return _GenOutcome(
                report_dump=report.model_dump(),
                raw_response=raw_response,
                is_fallback=False,
                policy_violations=[v.rule for v in remaining],
                tail_degraded=report.tail_degraded,
            )

    return _GenOutcome(
        report_dump=report.model_dump(),
        raw_response=raw_response,
        is_fallback=False,
        tail_degraded=report.tail_degraded,
    )


async def _retry_message_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
    prior: CoachMessageReport,
) -> tuple[CoachMessageReport, List[PolicyViolation]]:
    """Re-prompt once with fix instructions for the message-policy violations.
    Returns (report, remaining_violations). If the retry produces nothing usable,
    the prior report and its violations are kept (so a surviving overreach still
    forces the fallback upstream)."""
    fix_instructions = "\n".join(
        f"- {v.rule}: {v.fix_instruction}" for v in violations
    )
    retry_message = (
        "Your previous message had policy violations. Rewrite the message and the "
        "tail to fix these issues ONLY (keep everything else the same):\n"
        f"{fix_instructions}\n\nOriginal context:\n{original_user_message}"
    )
    try:
        result = await _call_message(client, system_prompt, retry_message)
        if result.stop_reason == "refusal":
            return prior, validate_message_policy(prior, pack)
        report = merge_report(parse_blocks(result.content_blocks))
    except (EmptyMessageError, anthropic.APIError) as e:
        logger.error("Coach message retry error: %s", e)
        return prior, validate_message_policy(prior, pack)
    return report, validate_message_policy(report, pack)


async def _retry_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
    pack: CoachContextPack,
    violations: List[PolicyViolation],
) -> tuple[CoachReportContent, List[PolicyViolation]]:
    """
    Re-prompt the LLM once with fix instructions for policy violations.
    Returns (content, remaining_violations). Never loops more than once.
    """
    fix_instructions = "\n".join(
        f"- {v.rule}: {v.fix_instruction}" for v in violations
    )
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


def _coerce_uuid(value) -> uuid.UUID:
    """Accept either a UUID or its string form. Postgres tolerates the latter
    via implicit cast; SQLite (used in tests) does not, so coerce up front."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


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


def _to_read(db_report: CoachReport) -> CoachReportRead:
    """Convert a DB CoachReport row into the read schema.

    The stored report is validated against the model for its schema-version family
    (ADR 0009): the A3 prose CoachMessageReport for 2.x rows, the legacy structured
    CoachReportContent otherwise. Keying off the stored row's own schema_version
    (not the active config) means old rows always read back in their own shape, so
    a config flip never mis-parses history (AC7).
    """
    meta = CoachReportMeta.model_validate(db_report.meta)
    if str(db_report.schema_version or "").startswith("2"):
        report = CoachMessageReport.model_validate(db_report.report)
    else:
        report = CoachReportContent.model_validate(db_report.report)
    return CoachReportRead(
        id=db_report.id,
        activity_id=db_report.activity_id,
        report=report,
        meta=meta,
        debug=CoachReportDebug(
            context_pack=db_report.context_pack or {},
            system_prompt=PROMPT_VERSIONS.get(meta.prompt_id, "unknown"),
            raw_llm_response=db_report.raw_llm_response,
        ),
        created_at=db_report.created_at,
    )
