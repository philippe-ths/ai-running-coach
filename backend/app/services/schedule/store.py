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
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.schemas.schedule import PlannedWeekShape, SpacingRule

logger = logging.getLogger(__name__)

ACTIVE = "active"
SUPERSEDED = "superseded"
DRAFTING = "drafting"
FAILED = "failed"


# How long a draft may sit in `drafting` before it is treated as abandoned.
# Generous next to a generation that takes about a minute: the cost of waiting is
# a slow retry, the cost of being too eager is two concurrently-billed drafts.
DRAFT_STALE_AFTER = timedelta(minutes=15)


def latest_plan(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """The runner's most recent plan row whatever its status.

    The status poll reads this: a draft in flight is deliberately invisible to
    `get_active_plan`, so the week keeps serving the previous plan (or free mode)
    while a new one is being written.

    `created_at` is `server_default=func.now()` — the TRANSACTION timestamp on
    Postgres, one-second resolution on SQLite — so it ties readily. The `id`
    tiebreaker is arbitrary but DETERMINISTIC, which is what a poll needs: an
    answer that does not flip between two calls that saw the same rows.
    """
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id)
        .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())
        .first()
    )


def draft_in_flight(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """A draft currently being written, or None — stale rows excluded.

    Asked as its own query rather than read off `latest_plan`, because it is the
    idempotency guard for a billed operation and must not depend on which of two
    same-second rows sorted first.

    A row older than `DRAFT_STALE_AFTER` is not in flight. Without that, a Redis
    hiccup or a worker that died before picking the job up would leave a row
    nothing ever moves, and every later tap would return it — the feature would
    be permanently stuck for that runner with no way back but a DB edit.
    """
    cutoff = datetime.now(timezone.utc) - DRAFT_STALE_AFTER
    plan = (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == DRAFTING)
        .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())
        .first()
    )
    if plan is None:
        return None
    created = plan.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and created < cutoff:
        fail_plan(db, plan, "abandoned: still drafting after the staleness window")
        return None
    return plan


def create_drafting_plan(
    db: Session, user_id: uuid.UUID, *, goal_race_id: Optional[uuid.UUID] = None
) -> TrainingPlan:
    """An empty plan row in `drafting`, created before the generation starts.

    It exists up front so the runner's client has something to poll and so a
    crashed worker leaves a visible `drafting` row rather than silence.

    The race it is built towards is resolved HERE when the caller does not name
    one (#884). Neither caller ever did, so every plan in production carried a
    null pointer, `delete_goal_race`'s detach could never fire, and a runner with
    two races had no way to tell which block belonged to which. Resolving it at
    the one place that creates these rows is what stops the two callers drifting
    into two answers.
    """
    if goal_race_id is None:
        target = plan_target_race(db, user_id, on_or_after=date.today())
        goal_race_id = target.id if target is not None else None
    plan = TrainingPlan(
        user_id=user_id,
        status=DRAFTING,
        goal_race_id=goal_race_id,
        rules=[],
        week_shapes=[],
        source="coach",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def activate_plan(
    db: Session, plan: TrainingPlan, *, stamp_generated: bool = True
) -> TrainingPlan:
    """Make this plan the runner's active one, superseding any predecessor.

    Two rows change together, so it is one transaction: the old plan stops being
    active in the same commit that makes the new one active. At no instant does
    the runner have two active plans or none.

    The outgoing plan is stamped `superseded_at` (#857), the recorded fact "this
    stopped being your plan then", which is what `previous_plan` orders on.
    The incoming one has it cleared, because a plan that is current was not
    stepped away from and must not sort into the history it just left.

    `stamp_generated` is False for a RESTORE. `generated_at` says when the plan's
    thinking was written, and a plan brought back is not newly written; re-dating
    it would report a plan drafted three weeks ago as fresh and would destroy the
    only field that says otherwise. The transition is recorded in
    `superseded_at`, which is a different fact and has its own column.
    """
    now = datetime.now(timezone.utc)
    db.query(TrainingPlan).filter(
        TrainingPlan.user_id == plan.user_id,
        TrainingPlan.status == ACTIVE,
        TrainingPlan.id != plan.id,
    ).update(
        {TrainingPlan.status: SUPERSEDED, TrainingPlan.superseded_at: now},
        synchronize_session=False,
    )
    plan.status = ACTIVE
    plan.superseded_at = None
    if stamp_generated:
        plan.generated_at = now
    db.commit()
    db.refresh(plan)
    return plan


def fail_plan(db: Session, plan: TrainingPlan, reason: str) -> TrainingPlan:
    """Mark a draft failed, visibly.

    A plan cannot degrade the way a report can — half a plan is worse than none,
    because the runner would act on it — so a rejected draft leaves a row saying
    so rather than a partial schedule.

    The reason is LOGGED rather than stored. Validator failures are internal
    ("week 2026-08-17 cannot satisfy its own rule ..."), and the runner is owed a
    plain "your coach could not write a plan just now", not the machinery. If a
    runner-facing reason is ever wanted it needs its own column and its own
    wording, which is a deliberate change rather than a leak of this text.
    """
    logger.warning("schedule: draft %s failed: %s", plan.id, reason)
    plan.status = FAILED
    db.commit()
    db.refresh(plan)
    return plan


def latest_failed_plan(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """The runner's most recent draft that failed, or None.

    Asked separately from `latest_plan` rather than read off it, because the
    question is different. `latest_plan` answers "where does the newest row
    stand" and breaks a same-instant tie on an arbitrary id, which is fine for a
    poll that only needs a stable answer. "Was the last thing that happened a
    failure" cannot be settled that way: it has to compare the failure against
    the plan it tried to replace, and a tie there would report the opposite.
    """
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == FAILED)
        .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())
        .first()
    )


def get_active_plan(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """The runner's current plan, or None — which is free mode, not an error."""
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == ACTIVE)
        .order_by(TrainingPlan.created_at.desc())
        .first()
    )


def previous_plan(db: Session, user_id: uuid.UUID) -> Optional[TrainingPlan]:
    """The plan the runner was training to before the current one (#857).

    "The previous plan" has to mean ONE thing once several have been superseded,
    and the honest answer is the one most recently stepped away from: not the most
    recently written, and not an arbitrary pick from the superseded set.
    Those come apart the moment a plan is restored: a restore makes an OLDER row
    current again, so the newest-written superseded row is then the one the
    runner has just come back from rather than the one they are on.

    Ordering on `superseded_at` gives the property the acceptance criteria ask
    for. Going back and going back again returns you to where you were each time,
    because every swap records its own transition and the reader asks about the
    last one rather than reconstructing it.

    Rows superseded before `superseded_at` existed carry a null and sort LAST:
    unknown is older than anything known, and a plan replaced before this feature
    shipped is by definition not the one just stepped away from.
    """
    return (
        db.query(TrainingPlan)
        .filter(TrainingPlan.user_id == user_id, TrainingPlan.status == SUPERSEDED)
        .order_by(
            TrainingPlan.superseded_at.desc().nullslast(),
            TrainingPlan.created_at.desc(),
            TrainingPlan.id.desc(),
        )
        .first()
    )


def restore_blocker(plan: TrainingPlan, *, today: Optional[date] = None) -> Optional[str]:
    """Why this plan cannot be brought back, or None. Runner-facing wording.

    Two refusals, and only two.

    A plan that is not SUPERSEDED is not a plan to go back to: the active one is
    already current, a draft is not written yet, and a failed one was never a
    plan at all. None of those is a state the restore control offers, so reaching
    this is a stale client or a hand-made request.

    A plan whose horizon has entirely passed is the more interesting refusal.
    Restoring it would be worse than doing nothing: the week would report a plan
    while holding no session, the headline would measure against a target that
    ran out weeks ago, and free mode (a real answer, not an empty state) would be
    replaced by a plan that says nothing. `horizon_end` is the last
    DAY the plan covers, so the test is against today rather than a week boundary.
    A null horizon means the plan never recorded its reach; that is not evidence
    it has expired, and refusing on unknown would block a legitimate restore.
    """
    today = today or date.today()
    if plan.status != SUPERSEDED:
        return "That plan is not one you can go back to."
    if plan.horizon_end is not None and plan.horizon_end < today:
        return (
            "That plan finished on "
            f"{plan.horizon_end.isoformat()}, so going back to it would leave "
            "you with nothing planned."
        )
    return None


def restore_plan(
    db: Session, plan: TrainingPlan, *, today: Optional[date] = None
) -> TrainingPlan:
    """Bring a superseded plan back, keeping the one it steps away from (#857).

    Symmetric by construction: this is the same `activate_plan` transition the
    coach's draft uses, so the plan being left behind is retained exactly the way
    this one was, and becomes what `previous_plan` offers next. Going back is
    itself something you can go back from.

    Nothing is copied. The plan keeps its identity, its sessions, their
    completions and its provenance, because the runner asked for the plan they
    were training to and a duplicate of it is a different plan, one whose ticked
    sessions would have to be either re-ticked or silently invented.
    """
    blocker = restore_blocker(plan, today=today)
    if blocker is not None:
        raise ValueError(blocker)
    return activate_plan(db, plan, stamp_generated=False)


def count_sessions_from(
    db: Session, user_id: uuid.UUID, plan_id: uuid.UUID, *, on_or_after: date
) -> int:
    """How many of a plan's sessions still lie ahead (#857).

    The one number that says whether going back to this plan would give the
    runner anything: a plan whose sessions have all been passed is intact but
    empty from here. Owner-scoped like every other read in this module, even
    though the caller has already resolved the plan to this owner.
    """
    return (
        db.query(PlannedSession)
        .filter(
            PlannedSession.user_id == user_id,
            PlannedSession.plan_id == plan_id,
            PlannedSession.window_end >= on_or_after,
        )
        .count()
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


def plan_target_race(
    db: Session, user_id: uuid.UUID, *, on_or_after: date
) -> Optional[GoalRace]:
    """The race a plan drafted now would be built towards, or None (#884).

    `A` is the runner's own word for the race the block is built for, so an A
    race wins over a nearer B or C — a runner does not restructure a marathon
    build around a parkrun three weeks out. Among equals the nearest wins, since
    that is the one the next twelve weeks actually reach.

    One definition, because two callers create drafting plans and a plan that
    recorded a different race depending on which button started it would be
    worse than one that recorded none.
    """
    races = list_goal_races(db, user_id, on_or_after=on_or_after)
    if not races:
        return None
    return next((race for race in races if race.priority == "A"), races[0])


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
