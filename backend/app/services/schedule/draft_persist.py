"""Storing an accepted schedule draft (#1100).

Split out of `draft.py` (#1100). Moved verbatim; `draft.py` re-imports `_persist`.
"""

from datetime import date, timedelta
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.user import User
from app.schemas.season import DraftLog
from app.services.schedule import store
from app.services.schedule.draft_contract import DraftedPlan
from app.services.schedule.effort import estimate_effort
from app.services.schedule.frames import phase_label
from app.services.schedule.shapes import write_shapes


def _persist(
    db: Session,
    user: User,
    plan: TrainingPlan,
    drafted: DraftedPlan,
    load_model,
    *,
    model_id: str,
    season_row: Any = None,
    frames: Sequence[Any] = (),
    concrete: Optional[set] = None,
    log: Optional[DraftLog] = None,
    usual_week: bool = False,
) -> None:
    """Write the accepted plan and make it the runner's active one.

    The concrete weeks are the model's sessions (repaired where needed); every
    later week is a shape written by code from the season (or, with no season, from
    the runner's usual week). A concrete week's phase
    is the frame's, so the horizon groups the two halves under one name.
    """
    log = log or DraftLog(model=model_id)
    frames_by_week = {frame.week_start: frame for frame in frames}
    concrete = concrete if concrete is not None else {w.week_start for w in drafted.weeks}
    weeks = [
        week.model_copy(
            update={
                "phase": (
                    phase_label(frames_by_week[week.week_start].phase)
                    if week.week_start in frames_by_week
                    else None
                )
                or week.phase
            }
        )
        for week in drafted.weeks
    ]
    # No season (the runner has no goal) leaves no phases to sketch from, so the
    # later weeks are sketched from their usual week rather than left empty.
    shapes, shape_shortfalls = write_shapes(
        frames, load_model, concrete, usual_week=usual_week
    )
    log.shortfalls = list(log.shortfalls) + shape_shortfalls

    plan.rules = [rule.model_dump(mode="json") for rule in drafted.rules]
    plan.preferences = [pref.model_dump(mode="json") for pref in drafted.preferences] or None
    plan.week_shapes = shapes + store.concrete_week_phases(weeks)
    reach = [w.week_start for w in weeks] + [s["week_start"] for s in shapes]
    reach = [d if isinstance(d, date) else date.fromisoformat(d) for d in reach]
    plan.horizon_end = max(reach) + timedelta(days=6) if reach else None
    # The model the draft ACTUALLY ran on, taken from the client rather than
    # re-read from config — the provenance gap `service._persist_report` still
    # carries and that this path has no reason to inherit.
    plan.model_id = model_id
    plan.season_id = season_row.id if season_row is not None else None
    plan.draft_log = log.model_dump(mode="json")

    for week in weeks:
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

    store.activate_plan(db, plan)
