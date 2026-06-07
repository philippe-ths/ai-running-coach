"""
Coach service — orchestrates context pack → LLM → validate → policy check → store.
"""

import json
import logging
import re
import uuid
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
from app.schemas.coach import CoachReportContent, CoachReportDebug, CoachReportMeta, CoachReportRead
from app.schemas.coach_context import CoachContextPack
from app.services.coach.belief_store import write_back_beliefs
from app.services.coach.context import build_context_pack
from app.services.coach.llm import AnthropicClient
from app.services.analysis.classifier import Classification, playbook_key
from app.services.coach.prompts import PROMPT_VERSIONS, build_system_prompt
from app.services.coach.validator import PolicyViolation, validate_policy

SCHEMA_VERSION = "1.2"


def get_active_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Return the cached report row for the *active* version of this activity.

    The active version is the current (COACH_PROMPT_ID, SCHEMA_VERSION) pair.
    Rows from older prompt/schema versions are retained but ignored here, so a
    version bump causes a clean cache miss and regeneration rather than serving
    a stale shape.
    """
    activity_uuid = _coerce_uuid(activity_id)
    return (
        db.query(CoachReport)
        .filter(
            CoachReport.activity_id == activity_uuid,
            CoachReport.prompt_id == settings.COACH_PROMPT_ID,
            CoachReport.schema_version == SCHEMA_VERSION,
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
    classification = Classification.from_metrics(activity.metrics)
    system_prompt = build_system_prompt(prompt_id, playbook_key(activity, classification))
    user_message = json.dumps(pack_dict, default=str)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    raw_response = ""
    policy_violations: List[str] = []
    is_fallback = False

    try:
        raw_response = await client.generate_json(
            system=system_prompt,
            user=user_message,
            max_tokens=1024,
        )
        # Strip markdown code fences if the model wraps its JSON
        cleaned = _strip_code_fences(raw_response)
        parsed = json.loads(cleaned)
        content = CoachReportContent.model_validate(parsed)

        # Policy validation — deterministic checks on LLM output
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
        is_fallback = True
        content = CoachReportContent(
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
        )

    meta = CoachReportMeta(
        confidence=pack.metrics.confidence,
        model_id=settings.COACH_MODEL_ID,
        prompt_id=prompt_id,
        schema_version=SCHEMA_VERSION,
        input_hash=input_hash,
        generated_at=datetime.now(timezone.utc),
        policy_violations=policy_violations,
    )

    # Store (version columns mirror meta so the cache can key on them)
    db_report = CoachReport(
        activity_id=activity_uuid,
        prompt_id=prompt_id,
        schema_version=SCHEMA_VERSION,
        report=content.model_dump(),
        meta=meta.model_dump(mode="json"),
        context_pack=pack_dict,
        raw_llm_response=raw_response,
        is_fallback=is_fallback,
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
    if not is_fallback:
        write_back_beliefs(db, activity, pack)

    return _to_read(db_report)


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
            max_tokens=1024,
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
    """Remove markdown code fences (```json ... ```) that LLMs sometimes add."""
    stripped = text.strip()
    # Match ```json\n...\n``` or ```\n...\n```
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _to_read(db_report: CoachReport) -> CoachReportRead:
    """Convert a DB CoachReport row into the read schema."""
    meta = CoachReportMeta.model_validate(db_report.meta)
    return CoachReportRead(
        id=db_report.id,
        activity_id=db_report.activity_id,
        report=CoachReportContent.model_validate(db_report.report),
        meta=meta,
        debug=CoachReportDebug(
            context_pack=db_report.context_pack or {},
            system_prompt=PROMPT_VERSIONS.get(meta.prompt_id, "unknown"),
            raw_llm_response=db_report.raw_llm_response,
        ),
        created_at=db_report.created_at,
    )
