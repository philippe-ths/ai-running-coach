"""
Coach chat service — conversational follow-up on a coach report.

Builds a system prompt from the same context pack that produced the report,
plus the athlete profile and recent trends, then streams a multi-turn
conversation via the LLM.
"""

import json
import logging
from datetime import date, timedelta
from typing import AsyncIterator, List

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.schemas.chat import ChatMessageRead
from app.services.coach.llm import AnthropicClient
from app.services.trends import _query_activity_facts

logger = logging.getLogger(__name__)


CHAT_SYSTEM_TEMPLATE = """You are a running coach continuing a conversation about a specific training session. The athlete has already received your initial analysis and may have follow-up questions.

CONTEXT — ACTIVITY & ANALYSIS:
{context_pack_json}

YOUR INITIAL REPORT:
{report_json}

ATHLETE PROFILE:
{profile_json}

RECENT TRAINING (last 30 days):
{trends_json}

RULES:
1. Answer based ONLY on the data provided above. Never invent facts.
2. NEVER diagnose injuries or medical conditions. If asked about pain, recommend professional assessment.
3. Reference specific numbers from the data when relevant (pace, HR, effort score, etc.).
4. Keep answers conversational but grounded — you are a knowledgeable coach, not a chatbot.
5. If the athlete asks about something not covered by the data, say so honestly.
6. ZONE LANGUAGE: If zones_calibrated is false in the metrics, NEVER reference specific HR zones (Z1-Z5). Use effort descriptions instead (easy, moderate, hard).
7. Be concise. Most answers should be 2-4 sentences unless the athlete asks for detail.
8. When discussing training recommendations, be conservative. Never recommend risky volume jumps.
9. You may suggest adjustments to the initial analysis if the athlete provides new context (e.g., "I was running on trails" or "I felt sick")."""


def _build_trends_summary(db: Session, activity: Activity) -> dict:
    """Build a compact trends summary for chat context."""
    activity_date = activity.start_date.date()
    today = date.today()
    end = min(today, activity_date + timedelta(days=1))
    start_30d = end - timedelta(days=30)

    facts = _query_activity_facts(db, start_30d, end)

    if not facts:
        return {"period": "30d", "activity_count": 0}

    total_dist = sum(f.distance_m for f in facts)
    total_time = sum(f.moving_time_s for f in facts)
    total_effort = sum(f.effort_score or 0 for f in facts)
    avg_effort = total_effort / len(facts) if facts else 0

    weekly_km = round(total_dist / 1000 / max((end - start_30d).days / 7, 1), 1)

    return {
        "period": "30d",
        "activity_count": len(facts),
        "total_distance_km": round(total_dist / 1000, 1),
        "total_time_min": round(total_time / 60),
        "weekly_avg_km": weekly_km,
        "avg_effort_score": round(avg_effort, 1),
        "total_effort": round(total_effort, 1),
    }


def _build_chat_system_prompt(
    context_pack: dict,
    report: dict,
    profile: dict,
    trends: dict,
) -> str:
    """Assemble the chat system prompt from all context sources."""
    return CHAT_SYSTEM_TEMPLATE.format(
        context_pack_json=json.dumps(context_pack, default=str),
        report_json=json.dumps(report, default=str),
        profile_json=json.dumps(profile, default=str),
        trends_json=json.dumps(trends, default=str),
    )


def get_chat_history(db: Session, activity_id: str) -> List[ChatMessageRead]:
    """Return all chat messages for an activity, ordered chronologically."""
    messages = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity_id)
        .order_by(CoachChatMessage.created_at.asc())
        .all()
    )
    return [ChatMessageRead.model_validate(m) for m in messages]


async def stream_chat_response(
    db: Session, activity_id: str, user_message: str
) -> AsyncIterator[str]:
    """
    Process a user message and stream the assistant response.

    Loads the coach report's context pack, builds a system prompt with
    full context, retrieves conversation history, and streams the LLM response.
    Saves both the user message and the full assistant response to the DB.
    """
    # Load the coach report (must exist before chatting)
    report_row = (
        db.query(CoachReport)
        .filter(CoachReport.activity_id == activity_id)
        .first()
    )
    if not report_row:
        yield "I don't have an analysis for this activity yet. Please generate the coach report first."
        return

    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        yield "Activity not found."
        return

    # Build context from the stored context pack
    context_pack = report_row.context_pack or {}
    report_content = report_row.report or {}

    # Get athlete profile
    profile = (
        db.query(UserProfile)
        .filter(UserProfile.user_id == activity.user_id)
        .first()
    )
    profile_dict = {}
    if profile:
        profile_dict = {
            "goal_type": profile.goal_type,
            "experience_level": profile.experience_level,
            "weekly_days_available": profile.weekly_days_available,
            "current_weekly_km": profile.current_weekly_km,
            "max_hr": profile.max_hr,
            "max_hr_source": getattr(profile, "max_hr_source", None),
            "injury_notes": profile.injury_notes,
        }

    # Build trends summary
    trends = _build_trends_summary(db, activity)

    # Build system prompt
    system_prompt = _build_chat_system_prompt(
        context_pack, report_content, profile_dict, trends
    )

    # Save the user message
    user_msg = CoachChatMessage(
        activity_id=activity_id,
        role="user",
        content=user_message,
    )
    db.add(user_msg)
    db.commit()

    # Load conversation history (including the message we just saved)
    history_rows = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.activity_id == activity_id)
        .order_by(CoachChatMessage.created_at.asc())
        .all()
    )

    # Build messages array for the LLM
    messages = [{"role": row.role, "content": row.content} for row in history_rows]

    # Stream the response
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.COACH_MODEL_ID,
    )

    full_response = []
    try:
        async for chunk in client.stream_chat(
            system=system_prompt,
            messages=messages,
            max_tokens=1024,
        ):
            full_response.append(chunk)
            yield chunk
    except Exception as e:
        logger.error("Chat streaming error: %s", e)
        error_msg = "Sorry, I encountered an error. Please try again."
        full_response = [error_msg]
        yield error_msg

    # Save the assistant response
    assistant_msg = CoachChatMessage(
        activity_id=activity_id,
        role="assistant",
        content="".join(full_response),
    )
    db.add(assistant_msg)
    db.commit()
