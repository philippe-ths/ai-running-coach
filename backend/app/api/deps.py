"""Owner-scoped route dependencies (#802, ADR 0022).

Tenant scoping used to be a convention repeated inside handler bodies: every
route that took a resource id fetched the row and checked the owner itself,
backed by four near-identical router-local helpers plus three raw inline copies.
A route added later inherited nothing and had to remember. That is the seam
where forgetting leaks another runner's data (#795).

Here ownership is a property of the ROUTE instead. A handler declares the owned
resource it operates on::

    def read_activity(activity: OwnedActivityDetail): ...

and the resolution happens before the body runs, visible in the signature and in
the generated OpenAPI schema. The same idiom the threads router already uses for
its kill switch (#784): put the guard where a route cannot forget it.

ADR 0022 stands: the anchor is the authenticated user, resolved server-side by
``require_current_user``. A client-supplied id can only narrow within the
caller's own data — it can never widen the scope. A cross-tenant id is
indistinguishable from a missing one (the same 404 covers both), so no
information about another runner's data leaks through the status or the detail.

Each owned resource type has ONE definition here. Where a route genuinely needs
a different load strategy or a different 404 detail, the difference is an
explicit named dependency rather than a flattened default:

- ``OwnedActivity``       lean ownership gate, no eager loads (write paths).
- ``OwnedActivityDetail`` full graph + soft-delete filter (the detail view).
- ``OwnedActivityWithMetrics`` also requires analysed metrics, and carries the
  regenerate endpoint's own 404 detail.
- ``LinkedStravaAccount`` / ``OptionalStravaAccount`` — one 404s, one no-ops.

Routes that take no client-supplied id (a list scoped to the caller, a per-user
singleton) need no gate; they carry ``CurrentUser`` and thread ``user.id`` into
a query helper whose ``user_id`` argument is required.
"""

from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models import Activity, Block, CoachingRelationship, GoalRace, StravaAccount, User, UserMaterial
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.models.thread import Thread
from app.services import activity_queries

# The two every owned-resource dependency is built from.
CurrentUser = Annotated[User, Depends(require_current_user)]
DbSession = Annotated[Session, Depends(get_db)]


# --- plain resolvers -------------------------------------------------------
#
# The FastAPI dependencies below are thin wrappers over these. They exist as
# plain functions so the handful of routes whose resource id arrives in the
# REQUEST BODY (block split/merge, the thread turn) resolve through the same
# single definition rather than growing a second copy of the rule.


def require_owned_activity(db: Session, activity_id: UUID, user: User) -> Activity:
    """The activity by id IF it belongs to ``user``; 404 otherwise.

    Lean: no eager loads, and soft-deleted state is deliberately not filtered —
    this is the ownership gate the coach/check-in/intent write paths use, which
    must reach the same set of activities they reached before P2.1.
    """
    activity = activity_queries.get_owned_activity(db, activity_id, user.id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


def require_owned_block(db: Session, block_id: UUID, user: User) -> Block:
    """The block by id IF it belongs to ``user``; 404 otherwise."""
    block = (
        db.query(Block)
        .filter(Block.id == block_id, Block.user_id == user.id)
        .first()
    )
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return block


def require_owned_thread(db: Session, thread_id: UUID, user: User) -> Thread:
    """The thread by id IF it belongs to ``user``; 404 otherwise."""
    from app.services.coach import threads as thread_service

    thread = thread_service.get_owned_thread(db, thread_id, user.id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


# --- activities ------------------------------------------------------------


def get_owned_activity(
    activity_id: UUID, db: DbSession, user: CurrentUser
) -> Activity:
    return require_owned_activity(db, activity_id, user)


OwnedActivity = Annotated[Activity, Depends(get_owned_activity)]


def get_owned_activity_detail(
    activity_id: UUID, db: DbSession, user: CurrentUser
) -> Activity:
    """The detail view's activity: the full graph in one query.

    A distinct dependency because the load strategy differs — metrics, check-in,
    streams and the deferred ``raw_summary`` are eager-loaded (#359), and a
    soft-deleted activity reads as absent (#410). Flattening this onto the lean
    gate would either N+1 the detail view or change which rows the write paths
    can reach.
    """
    activity = activity_queries.get_activity(db, activity_id, user_id=user.id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


OwnedActivityDetail = Annotated[Activity, Depends(get_owned_activity_detail)]


def get_owned_activity_with_metrics(
    activity_id: UUID, db: DbSession, user: CurrentUser
) -> Activity:
    """An owned activity that has analysed metrics to regenerate a report from.

    Carries its own 404 detail: the regenerate endpoint deliberately answers
    "not found or metrics not yet computed" for BOTH the cross-tenant and the
    not-yet-analysed case, so the two are indistinguishable from outside.
    """
    activity = activity_queries.get_owned_activity(db, activity_id, user.id)
    if activity is None or not activity.metrics:
        raise HTTPException(
            status_code=404,
            detail="Activity not found or metrics not yet computed.",
        )
    return activity


OwnedActivityWithMetrics = Annotated[
    Activity, Depends(get_owned_activity_with_metrics)
]


# --- blocks ----------------------------------------------------------------


def get_owned_block(block_id: UUID, db: DbSession, user: CurrentUser) -> Block:
    return require_owned_block(db, block_id, user)


OwnedBlock = Annotated[Block, Depends(get_owned_block)]


# --- threads ---------------------------------------------------------------


def get_owned_thread(thread_id: UUID, db: DbSession, user: CurrentUser) -> Thread:
    return require_owned_thread(db, thread_id, user)


OwnedThread = Annotated[Thread, Depends(get_owned_thread)]


# --- schedule --------------------------------------------------------------


def require_owned_goal_race(db: Session, race_id: UUID, user: User) -> GoalRace:
    """The goal race by id IF it belongs to ``user``; 404 otherwise."""
    race = (
        db.query(GoalRace)
        .filter(GoalRace.id == race_id, GoalRace.user_id == user.id)
        .first()
    )
    if race is None:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


def get_owned_goal_race(race_id: UUID, db: DbSession, user: CurrentUser) -> GoalRace:
    return require_owned_goal_race(db, race_id, user)


OwnedGoalRace = Annotated[GoalRace, Depends(get_owned_goal_race)]


def require_owned_planned_session(
    db: Session, session_id: UUID, user: User
) -> PlannedSession:
    """The planned session by id IF it belongs to ``user``; 404 otherwise.

    `PlannedSession.user_id` is denormalised off its plan precisely so this gate
    is one predicate rather than a join a later route could forget.
    """
    session = (
        db.query(PlannedSession)
        .filter(PlannedSession.id == session_id, PlannedSession.user_id == user.id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Planned session not found")
    return session


def get_owned_planned_session(
    session_id: UUID, db: DbSession, user: CurrentUser
) -> PlannedSession:
    return require_owned_planned_session(db, session_id, user)


OwnedPlannedSession = Annotated[
    PlannedSession, Depends(get_owned_planned_session)
]


def require_owned_training_plan(
    db: Session, plan_id: UUID, user: User
) -> TrainingPlan:
    """The training plan by id IF it belongs to ``user``; 404 otherwise (#857).

    Any status. Which statuses a given route will act on is that route's rule,
    not this gate's: flattening "not yours" and "not in a state you can restore"
    into one answer here would tell a runner their own plan does not exist.
    """
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.id == plan_id, TrainingPlan.user_id == user.id)
        .first()
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


def get_owned_training_plan(
    plan_id: UUID, db: DbSession, user: CurrentUser
) -> TrainingPlan:
    return require_owned_training_plan(db, plan_id, user)


OwnedTrainingPlan = Annotated[TrainingPlan, Depends(get_owned_training_plan)]


# --- user materials --------------------------------------------------------


def get_materials_owner_id(db: DbSession, user: CurrentUser) -> UUID:
    """The user id the materials routes scope every query to.

    Resolved through ``get_current_user_profile``, which also get-or-creates the
    runner's profile and coaching-relationship singletons — a side effect these
    routes have always had, kept here so it stays in exactly one place.
    """
    from app.api.profile import get_current_user_profile

    return get_current_user_profile(db, user).user_id


MaterialsOwnerId = Annotated[UUID, Depends(get_materials_owner_id)]


def get_owned_material(
    material_id: UUID, db: DbSession, owner_id: MaterialsOwnerId
) -> UserMaterial:
    """The material by id IF it belongs to the caller; 404 otherwise."""
    material = (
        db.query(UserMaterial)
        .filter(
            UserMaterial.user_id == owner_id, UserMaterial.id == material_id
        )
        .first()
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found.")
    return material


OwnedMaterial = Annotated[UserMaterial, Depends(get_owned_material)]


# --- Strava account --------------------------------------------------------


def _account_for(db: Session, user: User) -> Optional[StravaAccount]:
    return (
        db.query(StravaAccount)
        .filter(StravaAccount.user_id == user.id)
        .first()
    )


def get_linked_strava_account(
    db: DbSession, user: CurrentUser
) -> StravaAccount:
    """The caller's own linked Strava account; 404 when they have none."""
    account = _account_for(db, user)
    if account is None:
        raise HTTPException(
            status_code=404,
            detail="No linked Strava account found. Connect Strava first.",
        )
    return account


LinkedStravaAccount = Annotated[StravaAccount, Depends(get_linked_strava_account)]


def get_optional_strava_account(
    db: DbSession, user: CurrentUser
) -> Optional[StravaAccount]:
    """The caller's own linked Strava account, or None.

    Distinct from ``LinkedStravaAccount`` because the self-heal refresh answers
    200 ``{"status": "no_account"}`` rather than 404 when nothing is linked.
    """
    return _account_for(db, user)


OptionalStravaAccount = Annotated[
    Optional[StravaAccount], Depends(get_optional_strava_account)
]


# --- coaching relationship -------------------------------------------------


def get_coaching_relationship(
    db: DbSession, user: CurrentUser
) -> CoachingRelationship:
    """The caller's own coaching-relationship singleton, created if absent.

    ``get_current_user_profile`` race-safely get-or-creates the thin row (#598),
    so a runner who never edited their profile still has one to read/write.
    """
    from app.api.profile import get_current_user_profile

    profile = get_current_user_profile(db, user)
    return (
        db.query(CoachingRelationship)
        .filter(CoachingRelationship.user_id == profile.user_id)
        .first()
    )


CoachRelationship = Annotated[
    CoachingRelationship, Depends(get_coaching_relationship)
]
