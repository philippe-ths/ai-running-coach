"""
Coach service — orchestrates context pack → LLM → validate → store.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity
from app.models.coach_report import CoachReport
from app.schemas.coach import CoachReportContent, CoachReportMeta, CoachReportRead
from app.services.coach.context import build_context_pack, hash_context_pack
from app.services.coach.llm import AnthropicClient
from app.services.coach.prompts import PROMPT_VERSIONS

SCHEMA_VERSION = "1.0"


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

    # Call LLM
    prompt_id = settings.COACH_PROMPT_ID
    system_prompt = PROMPT_VERSIONS[prompt_id]
    user_message = json.dumps(pack, default=str)

    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    raw_response = ""
    try:
        raw_response = await client.generate_json(
            system=system_prompt,
            user=user_message,
            max_tokens=1024,
        )
        parsed = json.loads(raw_response)
        content = CoachReportContent.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, Exception):
        # Safe fallback
        content = CoachReportContent(
            key_takeaways=[
                "Analysis is temporarily unavailable for this activity.",
                "Your metrics have been recorded and can be reviewed in the detail view.",
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


def _to_read(db_report: CoachReport) -> CoachReportRead:
    """Convert a DB CoachReport row into the read schema."""
    return CoachReportRead(
        id=db_report.id,
        activity_id=db_report.activity_id,
        report=CoachReportContent.model_validate(db_report.report),
        meta=CoachReportMeta.model_validate(db_report.meta),
        created_at=db_report.created_at,
    )
