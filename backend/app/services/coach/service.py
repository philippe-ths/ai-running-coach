"""
Coach service — orchestrates context pack → LLM → validate → policy check → store.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.schemas.coach import CoachReportContent, CoachReportDebug, CoachReportMeta, CoachReportRead
from app.services.coach.context import build_context_pack, hash_context_pack
from app.services.coach.llm import AnthropicClient
from app.services.coach.prompts import PROMPT_VERSIONS, build_system_prompt
from app.services.coach.validator import PolicyViolation, validate_policy

SCHEMA_VERSION = "1.1"


async def get_or_generate_coach_report(
    db: Session, activity_id: str
) -> Optional[CoachReportRead]:
    """
    Returns cached report if one exists, otherwise generates a new one via LLM.
    """
    # Check cache
    existing = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity_id)
        .first()
    )
    if existing:
        return _to_read(existing)

    # Load activity
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity or not activity.metrics:
        return None

    # Build context pack
    pack = build_context_pack(db, activity)
    input_hash = hash_context_pack(pack)

    # Build prompt with activity-type playbook
    prompt_id = settings.COACH_PROMPT_ID
    activity_class = pack["metrics"].get("activity_class")
    system_prompt = build_system_prompt(prompt_id, activity_class)
    user_message = json.dumps(pack, default=str)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    raw_response = ""
    policy_violations: List[str] = []

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
                client, system_prompt, user_message, violations
            )
            if retry_violations:
                logger.warning(
                    "Policy violations persisted after retry: %s",
                    [v.rule for v in retry_violations],
                )
                policy_violations = [v.rule for v in retry_violations]

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Coach report parse/validation error: %s", e)
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
        confidence=pack["metrics"]["confidence"],
        model_id=settings.COACH_MODEL_ID,
        prompt_id=prompt_id,
        schema_version=SCHEMA_VERSION,
        input_hash=input_hash,
        generated_at=datetime.now(timezone.utc),
        policy_violations=policy_violations,
    )

    # Store
    db_report = CoachReport(
        activity_id=activity_id,
        report=content.model_dump(),
        meta=meta.model_dump(mode="json"),
        context_pack=pack,
        raw_llm_response=raw_response,
    )
    db.add(db_report)
    db.commit()
    db.refresh(db_report)

    return _to_read(db_report)


async def _retry_with_fixes(
    client: AnthropicClient,
    system_prompt: str,
    original_user_message: str,
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
        remaining = validate_policy(content, json.loads(original_user_message))
        return content, remaining

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Coach report retry parse error: %s", e)
        # Return the original violations — caller will use first attempt's content
        # Re-parse original to return something valid
        original_parsed = json.loads(_strip_code_fences(original_user_message))
        # This shouldn't happen in practice — fall through
        raise


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
