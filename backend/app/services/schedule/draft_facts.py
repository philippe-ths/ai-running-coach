"""What the schedule draft reads about the runner (#1100).

Split out of `draft.py` (#1100): the fact projection, the profile and memory
lines, and the settling-conversation block. Moved verbatim; `draft.py` re-imports
every name.
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.services.activity_facts import query_facts

# Keep the original logger name so prod log records are unchanged (#1100).
logger = logging.getLogger("app.services.schedule.draft")

# How much history the drafting context reads. The load model wants ~6 months; the
# volume norm wants its 12-week baseline plus the current week.
_HISTORY_DAYS = 200


def fetch_draft_facts(db: Session, user: User, today: date) -> List[Any]:
    """The one fact projection a draft needs.

    It was fetched twice — once to write the context and again for the load model
    and the running norm — which is the duplication `activity_facts.scan_cache`
    exists to collapse for the coach pack.
    """
    return query_facts(
        db,
        today - timedelta(days=_HISTORY_DAYS),
        today + timedelta(days=1),
        user_id=user.id,
    )


def _profile_lines(user: User, profile: Any) -> List[str]:
    lines = []
    if profile is None:
        return ["Nothing stated yet."]
    for label, value in (
        ("Goal", getattr(profile, "goal_type", None)),
        ("Experience", getattr(profile, "experience_level", None)),
        ("Days available each week", getattr(profile, "weekly_days_available", None)),
        (
            # #1080: the runner's own limit. Enforced on every plan, so the coach
            # is told it is theirs and that walks count, not left to guess.
            "Most activities in a day (their own setting, enforced; walks count)",
            getattr(profile, "max_activities_per_day", None),
        ),
        ("Current weekly km (stated)", getattr(profile, "current_weekly_km", None)),
        ("Max HR", getattr(profile, "max_hr", None)),
        ("Injury notes", getattr(profile, "injury_notes", None)),
        ("Weight (kg)", getattr(profile, "weight_kg", None)),
        ("Height (cm)", getattr(profile, "height_cm", None)),
    ):
        if value not in (None, "", []):
            lines.append(f"- {label}: {value}")
    return lines or ["Nothing stated yet."]


# How much of the settling conversation the draft reads. Bounded like every other
# transcript this project puts in front of a model, but wider than the continuity
# digests: those carry a flavour of what was discussed, this carries the plan
# itself, and truncating the message that holds the block would silently drop the
# thing the runner confirmed.
_MAX_TRANSCRIPT_TURNS = 16
_MAX_TRANSCRIPT_CHARS = 2500


def _conversation_block(db: Session, user: User, thread_id: Optional[str]) -> str:
    """The settling conversation, owner-scoped, or "" when there is none (#856).

    Owner-scoped for the usual reason and one specific to this path: the thread id
    arrives as a job argument rather than from an authenticated request, so it is
    re-checked against the plan's owner here rather than trusted.
    """
    if not thread_id:
        return ""
    try:
        from app.models.coach_chat_message import CoachChatMessage
        from app.models.thread import Thread

        thread = (
            db.query(Thread)
            .filter(Thread.id == uuid.UUID(str(thread_id)), Thread.user_id == user.id)
            .first()
        )
        if thread is None:
            logger.warning(
                "schedule draft: thread %s is not this runner's; drafting unseeded",
                thread_id,
            )
            return ""
        from app.services.coach.threads import said

        rows = (
            db.query(CoachChatMessage)
            .filter(
                CoachChatMessage.thread_id == thread.id,
                # This block is the conversation that settled the plan, and each
                # line is attributed to Runner or You. An action event (#778) or
                # a failed turn (#1124) is neither, so it is read out of the
                # transcript rather than attributed to whichever side the
                # else-branch happens to name.
                said(),
            )
            .order_by(
                CoachChatMessage.created_at.desc(), CoachChatMessage.id.desc()
            )
            .limit(_MAX_TRANSCRIPT_TURNS)
            .all()
        )
    except Exception:  # noqa: BLE001 — an unreadable transcript is not a failed plan
        logger.exception("schedule draft: could not read thread %s", thread_id)
        return ""

    lines: List[str] = []
    for row in reversed(rows):  # chronological
        text = " ".join((row.content or "").split())
        if not text:
            continue
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            text = text[:_MAX_TRANSCRIPT_CHARS].rstrip() + "…"
        lines.append(f"{'Runner' if row.role == 'user' else 'You'}: {text}")
    if not lines:
        return ""
    return "\n\n## THE CONVERSATION\n\n" + "\n\n".join(lines)


def _memory_lines(db: Session, user: User) -> List[str]:
    if not settings.COACH_MEMORY_ENABLED:
        return []
    try:
        from app.services.coach.memory_store import get_memory

        row = get_memory(db, user.id)
    except Exception:
        return []
    if row is None or not getattr(row, "profile", None):
        return []
    lines: List[str] = []
    profile = row.profile
    if isinstance(profile, dict):
        for section in (
            "who_you_are",
            "limits_and_constraints",
            "goals_and_plans",
            "what_works_for_you",
        ):
            for entry in (profile.get(section) or [])[:4]:
                lines.append(f"- {entry}")
    return lines[:16]
