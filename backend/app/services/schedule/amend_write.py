"""Writing an amendment: normalising the tool result, swapping the window's
sessions in one transaction, and describing what changed (#981).

Split out of `amend.py`; import everything from there.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.services.schedule import store
from app.services.schedule.amend_contract import SUMMARY_MAX_CHARS, AmendedPlan
from app.services.schedule.amend_window import _is_replaceable, sessions_in_window
from app.services.schedule.effort import estimate_effort
from app.services.schedule.week_check import says_week

# Named for `amend` so the moved `_apply` log line keeps its logger (#1098).
logger = logging.getLogger("app.services.schedule.amend")


def _restate_shortfalls(
    plan: TrainingPlan, rewritten: List[date], shortfalls: Sequence[str]
) -> None:
    """Make the plan's stored shortfalls describe the plan as it now stands.

    The lines about the weeks the amendment rewrote are replaced by what is still
    short in them now (nothing, when the rewrite holds), and every other week's
    line stays. Left alone, the horizon and the coach went on saying a week was
    short that the runner had just fixed. Written in the amendment's own
    transaction, so the sessions and the sentence about them land together.
    """
    log = dict(plan.draft_log or {})
    kept = [
        line
        for line in (log.get("shortfalls") or [])
        if not any(says_week(line, week) for week in rewritten)
    ]
    log["shortfalls"] = [*kept, *shortfalls]
    plan.draft_log = log  # reassigned: an in-place edit of a JSON column is not seen


def _normalise(raw: Any) -> Any:
    """The drafted-plan normaliser's amendment-shaped sibling: tolerate a tool
    result handed back as a JSON string, and trim the one cosmetic field.

    The summary is trimmed for the same reason the drafted plan's is: it is a
    note ABOUT the amendment rather than part of it, so a long one is not a
    reason to lose the weeks. Everything structural is still left exactly as
    the model produced it, because quietly repairing a session would mean
    storing something the coach did not actually say.
    """
    import json

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return raw
    if isinstance(raw, dict):
        summary = raw.get("summary")
        if isinstance(summary, str) and len(summary) > SUMMARY_MAX_CHARS:
            raw = {**raw, "summary": summary[:SUMMARY_MAX_CHARS].rstrip()}
    return raw


def _apply(
    db: Session,
    user: User,
    plan: TrainingPlan,
    amended: AmendedPlan,
    load_model,
    *,
    start: date,
    end: date,
    today: date,
    shortfalls: Sequence[str] = (),
) -> tuple:
    """Swap the window's replaceable sessions for the amended ones, in one
    transaction, on the plan the runner is already following.

    The plan row keeps its identity: same id, same rules, same race, same
    `generated_at`. A restore still finds the plan it always did, and the
    completions attached to sessions outside the window are untouched because
    those rows are never loaded.

    The week shapes for weeks now written out are dropped, since a week holding
    real sessions is `planned` and a leftover shape beside it would be a second
    answer about the same week.
    """
    removed: List[str] = []
    for row in sessions_in_window(db, user.id, plan, start, end):
        if not _is_replaceable(row, today):
            continue
        removed.append(_describe_row(row))
        db.delete(row)
    replaced = len(removed)

    added: List[str] = []
    written = 0
    for week in amended.weeks:
        for session in week.sessions:
            db.add(
                PlannedSession(
                    plan_id=plan.id,
                    user_id=user.id,
                    window_start=session.window_start,
                    window_end=session.window_end,
                    intent=session.intent,
                    discipline=session.discipline,
                    commitment=session.commitment,
                    title=session.title,
                    detail=session.detail,
                    target_distance_m=session.target_distance_m,
                    target_duration_s=session.target_duration_s,
                    target_effort_score=estimate_effort(
                        load_model,
                        session.discipline,
                        duration_s=session.target_duration_s,
                        distance_m=session.target_distance_m,
                    ),
                    structure=session.structure(),
                    alternatives=session.alternatives_json(),
                    activity_type=session.activity_type,
                )
            )
            added.append(f"{session.window_start.strftime('%a')} {session.title}")
            written += 1

    written_weeks = {w.week_start for w in amended.weeks}
    plan.week_shapes = [
        shape
        for shape in (plan.week_shapes or [])
        if _shape_week(shape) not in written_weeks
    ] + store.concrete_week_phases(amended.weeks)
    # `horizon_end` is a floor on the plan's reach, so it only ever grows here:
    # an amendment that writes sessions past it has extended the plan, and one
    # that writes inside it has not shortened anything.
    if plan.horizon_end is None or plan.horizon_end < end:
        plan.horizon_end = end
    _restate_shortfalls(plan, sorted(written_weeks), shortfalls)
    db.commit()
    logger.info(
        "schedule: amended plan %s over %s..%s (%s replaced, %s written)",
        plan.id, start, end, replaced, written,
    )
    return written, _describe_change(removed, added)


def _describe_row(row: Any) -> str:
    """One session the amendment removed, in the terms the runner reads."""
    return f"{row.window_start.strftime('%a')} {row.title}"


def _describe_change(removed: List[str], added: List[str]) -> List[str]:
    """What the window actually holds now that it did not, and vice versa.

    Compared as SETS of "day + title", so a session the rewrite reproduced
    unchanged is not reported as churn. What is left is the real difference, and
    it is what the ledger records and the runner is shown, in place of the card's
    forecast.
    """
    gone = [r for r in removed if r not in set(added)]
    new = [a for a in added if a not in set(removed)]
    lines: List[str] = []
    if gone:
        lines.append("Removed: " + "; ".join(gone))
    if new:
        lines.append("Added: " + "; ".join(new))
    if not lines:
        lines.append("No session in these weeks changed.")
    return lines


def _shape_week(shape: Any) -> Optional[date]:
    raw = shape.get("week_start") if isinstance(shape, dict) else None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None
