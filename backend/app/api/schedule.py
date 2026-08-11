"""The schedule API (#830): what the runner is doing next.

Read-only over the plan itself in this slice — the plan is written by the coach,
not by a form, so there is no create-a-session endpoint here and there never will
be one. The runner's own goal race IS theirs to state, so that is the one thing
this router writes.

The kill switch is a ROUTER-level dependency (`COACH_THREADS_ENABLED`'s shape),
so a route added later cannot forget it. Ownership is a property of the route:
the one path parameter that names an owned resource resolves through
`deps.get_owned_goal_race`, which `tests/test_route_ownership_802.py` enforces
structurally. Every other read carries `CurrentUser` into a store function whose
`user_id` argument is required.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, DbSession, OwnedGoalRace
from app.core.config import settings
from app.schemas.schedule import (
    GoalRaceCreate,
    GoalRaceRead,
    ScheduleHorizonRead,
    ScheduleWeekRead,
)
from app.services.schedule import store
from app.services.schedule.horizon import (
    DEFAULT_HORIZON_WEEKS,
    MAX_HORIZON_WEEKS,
    build_horizon,
)
from app.services.schedule.week import build_week

logger = logging.getLogger(__name__)


def require_schedule_enabled() -> None:
    """#830: the schedule surface's kill switch, applied to the whole router."""
    if not settings.SCHEDULE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "The schedule is temporarily unavailable. Your runs still sync, "
                "your analysis still updates, and your reports still arrive after "
                "each session."
            ),
        )


router = APIRouter(
    prefix="/schedule", dependencies=[Depends(require_schedule_enabled)]
)


@router.get("/week", response_model=ScheduleWeekRead)
def read_week(
    db: DbSession,
    user: CurrentUser,
    week_start: Optional[date] = Query(
        default=None,
        description=(
            "Any day in the week to read. Defaults to the current week. The week "
            "boundary is the runner's own (`week_starts_on`)."
        ),
    ),
) -> ScheduleWeekRead:
    """The week: what is planned, what is logged, and how it sits against normal.

    With no active plan this is free mode — the same shape with nothing committed
    on it, the runner's own norm in place of a target.
    """
    return build_week(db, user, target_week=week_start)


@router.get("/horizon", response_model=ScheduleHorizonRead)
def read_horizon(
    db: DbSession,
    user: CurrentUser,
    weeks: int = Query(
        default=DEFAULT_HORIZON_WEEKS, ge=1, le=MAX_HORIZON_WEEKS
    ),
) -> ScheduleHorizonRead:
    """The shape of the block: load per week, concrete near-term, shape beyond."""
    return build_horizon(db, user, weeks=weeks)


@router.get("/races", response_model=list[GoalRaceRead])
def list_races(
    db: DbSession,
    user: CurrentUser,
    include_past: bool = Query(default=False),
) -> list[GoalRaceRead]:
    # A day's grace on the "future" boundary. There is no stored runner timezone
    # to resolve a local day from (`activity_facts.local_day` derives it from an
    # activity, and a race has none), so a strict `today` would drop a race being
    # run TODAY from the default list for any runner west of the server. Erring a
    # day wide shows one stale race; erring narrow hides today's.
    cutoff = None if include_past else date.today() - timedelta(days=1)
    races = store.list_goal_races(db, user.id, on_or_after=cutoff)
    return [GoalRaceRead.model_validate(race) for race in races]


@router.post("/races", response_model=GoalRaceRead, status_code=201)
def create_race(
    body: GoalRaceCreate, db: DbSession, user: CurrentUser
) -> GoalRaceRead:
    """The runner states their own race. A plan is anchored to it, never the
    other way round — the coach does not decide what the runner is training for."""
    race = store.create_goal_race(
        db,
        user.id,
        name=body.name,
        race_date=body.race_date,
        distance_m=body.distance_m,
        priority=body.priority,
    )
    return GoalRaceRead.model_validate(race)


@router.delete("/races/{race_id}", status_code=204)
def delete_race(race: OwnedGoalRace, db: DbSession) -> None:
    store.delete_goal_race(db, race)
