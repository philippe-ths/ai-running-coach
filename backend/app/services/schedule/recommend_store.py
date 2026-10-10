"""Reading and writing the recommended week the runner was last shown (#1082)."""

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.week_recommendation import WeekRecommendation
from app.services.schedule.recommend import Recommendation


def load(db: Session, user_id: Any, week_start: date, plan_id: Any) -> Optional[WeekRecommendation]:
    row = (
        db.query(WeekRecommendation)
        .filter(WeekRecommendation.user_id == user_id, WeekRecommendation.week_start == week_start)
        .first()
    )
    if row is not None and row.plan_id != plan_id:
        # A new plan is a new week to recommend: its sessions are not the ones
        # the old assignment and its moves were about.
        row.assignment, row.changes, row.plan_id = {}, [], plan_id
    return row


def previous_days(row: Optional[WeekRecommendation]) -> Dict[uuid.UUID, date]:
    """The last shown day of each session, keyed like the session rows' ids."""
    if row is None:
        return {}
    out = {}
    for session_id, day in (row.assignment or {}).items():
        try:
            out[uuid.UUID(session_id)] = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
    return out


def describe_change(change: dict) -> str:
    """One move in plain words."""
    try:
        before = date.fromisoformat(change["from"]).strftime("%a")
        after = date.fromisoformat(change["to"]).strftime("%a")
    except (KeyError, TypeError, ValueError):
        return ""
    title = change.get("title") or "A session"
    if change.get("missed"):
        return f"{title} not done {before}, moved to {after}"
    return f"{title} moved from {before} to {after}"


def save(
    db: Session,
    row: Optional[WeekRecommendation],
    *,
    user_id: Any,
    week_start: date,
    plan_id: Any,
    rec: Recommendation,
    titles: Dict[str, str],
    today: date,
) -> WeekRecommendation:
    """Store what was shown, appending this read's moves to the week's changes.
    Writes only when something differs, so a plain re-read stays a read."""
    assignment = {str(sid): p.day.isoformat() for sid, p in rec.placements.items()}
    new_changes: List[dict] = [
        {
            "session_id": str(sid),
            "title": titles.get(str(sid), "A session"),
            "from": before.isoformat(),
            "to": after.isoformat(),
            "missed": before < today,
        }
        for sid, before, after in rec.moved
    ]
    if row is None:
        row = WeekRecommendation(
            user_id=user_id, week_start=week_start, plan_id=plan_id, assignment={}, changes=[]
        )
        db.add(row)
    if row.assignment == assignment and not new_changes:
        return row
    row.assignment = assignment
    row.changes = list(row.changes or []) + new_changes
    db.commit()
    return row
