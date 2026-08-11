"""Owner-scoped reads and writes for the schedule (#830).

Every function here takes a REQUIRED `user_id`. There is no unscoped read of a
plan, a session or a race — the shape `deps.py` describes for routes that carry
no client-supplied id ("thread `user.id` into a query helper whose `user_id`
argument is required") applied to the service layer.

The two JSON columns are strict-coerced on the way out, off-shape rows SKIPPED
rather than raising: the same containment `retrieval.fetch_corpus` uses for a
runner's distilled materials. A single malformed rule must not take down the
whole screen, and it must not be silently treated as a rule either — dropping it
means it does not constrain anything, and the checker reports what it can see.
"""

import logging
import uuid
from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.schemas.schedule import PlannedWeekShape, SpacingRule

logger = logging.getLogger(__name__)

ACTIVE = "active"
SUPERSEDED = "superseded"


def get_active_plan(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """The runner's current plan, or None — which is free mode, not an error."""
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == ACTIVE)
        .order_by(TrainingPlan.created_at.desc())
        .first()
    )


def sessions_in_range(
    db: Session,
    user_id: uuid.UUID,
    start: date,
    end: date,
    *,
    plan_id: Optional[uuid.UUID] = None,
) -> List[PlannedSession]:
    """Sessions whose window OPENS within `[start, end]`, inclusive.

    A window lies within one week by construction (validated on write), so
    filtering on `window_start` places each session in exactly one week and no
    session is counted twice in a horizon roll-up.
    """
    query = db.query(PlannedSession).filter(
        PlannedSession.user_id == user_id,
        PlannedSession.window_start >= start,
        PlannedSession.window_start <= end,
    )
    if plan_id is not None:
        query = query.filter(PlannedSession.plan_id == plan_id)
    return query.order_by(
        PlannedSession.window_start.asc(), PlannedSession.created_at.asc()
    ).all()


def plan_rules(plan: Optional[TrainingPlan]) -> List[SpacingRule]:
    """The plan's rules, strict-coerced; anything off-shape is dropped."""
    if plan is None or not plan.rules:
        return []
    coerced: List[SpacingRule] = []
    for raw in plan.rules:
        try:
            coerced.append(SpacingRule.model_validate(raw))
        except Exception:
            logger.warning(
                "schedule: dropping off-shape spacing rule on plan %s", plan.id
            )
    return coerced


def plan_week_shapes(plan: Optional[TrainingPlan]) -> List[PlannedWeekShape]:
    """The horizon's week shapes, strict-coerced; anything off-shape is dropped."""
    if plan is None or not plan.week_shapes:
        return []
    coerced: List[PlannedWeekShape] = []
    for raw in plan.week_shapes:
        try:
            coerced.append(PlannedWeekShape.model_validate(raw))
        except Exception:
            logger.warning(
                "schedule: dropping off-shape week shape on plan %s", plan.id
            )
    return coerced


# --- goal races ------------------------------------------------------------


def list_goal_races(
    db: Session, user_id: uuid.UUID, *, on_or_after: Optional[date] = None
) -> List[GoalRace]:
    query = db.query(GoalRace).filter(GoalRace.user_id == user_id)
    if on_or_after is not None:
        query = query.filter(GoalRace.race_date >= on_or_after)
    return query.order_by(GoalRace.race_date.asc()).all()


def create_goal_race(
    db: Session,
    user_id: uuid.UUID,
    *,
    name: str,
    race_date: date,
    distance_m: float,
    priority: str,
) -> GoalRace:
    race = GoalRace(
        user_id=user_id,
        name=name,
        race_date=race_date,
        distance_m=distance_m,
        priority=priority,
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def delete_goal_race(db: Session, race: GoalRace) -> None:
    """Detach the race from any plan that points at it, then remove it.

    A plan outliving its race is fine — it keeps its sessions and its shape, it
    simply stops being anchored. Deleting the plan too would throw away weeks of
    work because the runner corrected a date.

    The detach is owner-scoped as well as race-scoped. The race id is a UUID the
    route has already resolved to this owner, so the extra predicate changes no
    result today — it is there because "every query in this module names its
    owner" has to hold without exception to be worth anything, and an unscoped
    UPDATE is the shape #795 came from.
    """
    db.query(TrainingPlan).filter(
        TrainingPlan.goal_race_id == race.id,
        TrainingPlan.user_id == race.user_id,
    ).update({TrainingPlan.goal_race_id: None}, synchronize_session=False)
    db.delete(race)
    db.commit()
