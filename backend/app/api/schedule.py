"""The schedule API (#830): what the runner is doing next.

Read-only over the plan itself in this slice — the plan is written by the coach,
not by a form, so there is no create-a-session endpoint here and there never will
be one. The runner's own goal race IS theirs to state, so that is the one thing
this router writes.

The kill switch is a ROUTER-level dependency (`COACH_THREADS_ENABLED`'s shape),
so a route added later cannot forget it. Ownership is a property of the route:
every path parameter that names an owned resource resolves through its
`deps.get_owned_*` dependency, which `tests/test_route_ownership_802.py` enforces
structurally. Every other read carries `CurrentUser` into a store function whose
`user_id` argument is required.

#857 adds the one write here that is not the runner's own record of their week:
restoring a plan the coach replaced. It is still not a form for editing a plan
(it selects between plans the coach wrote), and it is the undo the rest of the
proposed-action set already had.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import (
    CurrentUser,
    DbSession,
    OwnedGoalRace,
    OwnedPlannedSession,
    OwnedTrainingPlan,
)
from app.core.config import settings
from app.schemas.schedule import (
    AmendmentStatusRead,
    DraftStatusRead,
    GoalRaceCreate,
    GoalRaceRead,
    PreviousPlanRead,
    ScheduleHorizonRead,
    ScheduleWeekRead,
)
from app.services.schedule import amend_watch, completion, store
from app.services.schedule.draft import enqueue_draft
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


# One sentence per failure CATEGORY (#859). A draft can fail for reasons the
# runner has genuinely different moves for, and until now all of them arrived as
# the same sentence: someone whose coach had ramped too hard was told to "ask
# again", which produces the same plan and the same rejection.
#
# What each sentence is held to: it says what happened in the runner's own terms,
# it names the move available to them, it states that nothing they had has
# changed, and it carries none of the gate's machinery. The category is chosen in
# `draft.py` where the failure is known; nothing internal ever travels here.
_DRAFT_FAILURE_MESSAGES = {
    store.FAILURE_TOO_BIG_A_JUMP: (
        "Your coach wrote a block that climbs much faster than your recent weeks, "
        "so it was not saved. Nothing has changed — ask again for a gentler "
        "build, or talk it through and have the same block spread over more weeks."
    ),
    store.FAILURE_UNREACHABLE: (
        "Your coach could not be reached, so no plan was written. Nothing has "
        "changed — try again in a few minutes."
    ),
    store.FAILURE_OVER_BUDGET: (
        "You have used this period's coaching allowance, so no plan was written. "
        "Nothing has changed — your runs still sync and your reports still "
        "arrive. Ask again once the allowance resets."
    ),
    # The fallback, and the sentence every failure used to get. No "just now"
    # (#879). This is read at the moment of failure and for as long afterwards as
    # the runner has not asked for another plan, which can be days: only the
    # empty-week panel used to show it, and only to someone sitting there
    # watching. Now it is on the Schedule screen of anyone whose last attempt
    # failed, so it has to stay true when it is no longer news.
    store.FAILURE_UNKNOWN: (
        "Your coach could not write a plan. Nothing has changed — ask again, or "
        "talk it through in a conversation."
    ),
}

_DRAFT_MESSAGES = {
    "drafting": "Your coach is writing your plan. This usually takes a minute.",
    "active": "Your plan is ready.",
    "superseded": "This plan has been replaced by a newer one.",
    "failed": _DRAFT_FAILURE_MESSAGES[store.FAILURE_UNKNOWN],
}


def _failure_message(plan) -> str:
    """The sentence for THIS failure. Falls back for a row with no category —
    every plan that failed before the column existed, and any category a future
    writer adds without a sentence to go with it."""
    return _DRAFT_FAILURE_MESSAGES.get(
        getattr(plan, "failure_kind", None) or store.FAILURE_UNKNOWN,
        _DRAFT_FAILURE_MESSAGES[store.FAILURE_UNKNOWN],
    )


def _draft_status(plan) -> DraftStatusRead:
    if plan is None:
        return DraftStatusRead(message="You have no plan yet.")
    known = plan.status in _DRAFT_MESSAGES
    # One fallback, not two. Reporting `status=None` beside a real `plan_id` and
    # a "you have no plan yet" message would be three answers to one question the
    # moment a fifth status is added.
    #
    # The message is still the WHOLE runner-facing surface: the category chooses
    # which sentence, and `DraftStatusRead` deliberately gains no reason field,
    # so there is no second channel a client could render raw.
    if plan.status == store.FAILED:
        message = _failure_message(plan)
    else:
        message = _DRAFT_MESSAGES.get(plan.status, "You have no plan yet.")
    return DraftStatusRead(
        status=plan.status if known else None,
        plan_id=plan.id if known else None,
        generated_at=plan.generated_at if known else None,
        message=message,
    )


@router.post("/draft", response_model=DraftStatusRead, status_code=202)
def start_draft(db: DbSession, user: CurrentUser) -> DraftStatusRead:
    """Ask the coach to write a plan.

    Runner-triggered by design: no background pass ever spends tokens writing
    plans for runners who have not asked. Returns immediately — the generation is
    a slow LLM call on the worker, and the client polls `GET /draft`.

    Idempotent while one is in flight, the `POST /api/strava/import` precedent: a
    second tap returns the draft already running rather than starting a second.
    """
    existing = store.draft_in_flight(db, user.id)
    if existing is not None:
        return _draft_status(existing)

    plan = store.create_drafting_plan(db, user.id)
    enqueue_draft(user.id, plan.id)
    return _draft_status(plan)


@router.get("/draft", response_model=DraftStatusRead)
def read_draft_status(db: DbSession, user: CurrentUser) -> DraftStatusRead:
    """Where the runner's most recent plan stands. Polled while drafting."""
    return _draft_status(store.latest_plan(db, user.id))


@router.get("/amendment", response_model=AmendmentStatusRead)
def read_amendment_status(user: CurrentUser) -> AmendmentStatusRead:
    """Whether a confirmed amendment is being written. Polled after a tap (#1003).

    Takes no database session: the whole answer is the in-flight state, and an
    amendment deliberately leaves no row to read while it runs.
    """
    state = amend_watch.current(user.id)
    if not state:
        return AmendmentStatusRead()
    return AmendmentStatusRead(
        status=state.get("status"),
        start=state.get("start"),
        end=state.get("end"),
        changes=state.get("changes") or [],
        detail=state.get("detail") or None,
    )


@router.post("/amendment/seen", status_code=204)
def clear_amendment_status(user: CurrentUser) -> None:
    """Stop reporting the last outcome, once a surface has shown it.

    Without this the finished state keeps answering until it expires, and every
    surface that mounts inside the window re-announces a change the runner
    already watched land.
    """
    amend_watch.clear(user.id)


_NO_PREVIOUS_PLAN = "You have no earlier plan to go back to."
_PREVIOUS_PLAN_AVAILABLE = (
    "Your previous plan is still here. Going back to it keeps this one, so you "
    "can swap again."
)


@router.get("/plans/previous", response_model=PreviousPlanRead)
def read_previous_plan(db: DbSession, user: CurrentUser) -> PreviousPlanRead:
    """The plan the runner was training to before this one (#857).

    Writing a plan supersedes the one it replaces and nothing destroys it, but
    until now nothing could reach it either, which made drafting the only proposed
    action in the set that did not come back with a tap. This read is what makes the way back
    visible on the screen the replacement landed on.
    """
    plan = store.previous_plan(db, user.id)
    if plan is None:
        return PreviousPlanRead(message=_NO_PREVIOUS_PLAN)
    blocker = store.restore_blocker(plan)
    return PreviousPlanRead(
        plan_id=plan.id,
        superseded_at=plan.superseded_at,
        generated_at=plan.generated_at,
        horizon_end=plan.horizon_end,
        sessions_ahead=store.count_sessions_from(
            db, user.id, plan.id, on_or_after=date.today()
        ),
        restorable=blocker is None,
        message=blocker or _PREVIOUS_PLAN_AVAILABLE,
    )


@router.post("/plans/{plan_id}/restore", status_code=204)
def restore_plan(plan: OwnedTrainingPlan, db: DbSession) -> None:
    """Go back to a plan that was replaced.

    The id is in the PATH rather than implied, and it is the id the runner was
    shown. If a new plan landed between the read and the tap, this restores the
    plan they agreed to or fails. It never quietly restores a different one.

    Symmetric and non-destructive, the property the whole issue is about: the
    plan being stepped away from is retained exactly the way this one was, and
    becomes what `GET /plans/previous` offers next.

    204 rather than the plan, matching the session writes above. A restore
    changes the week's headline, its sessions, its rules and its horizon at once,
    so handing back one object would invite a client to patch state it cannot
    correctly patch.
    """
    try:
        store.restore_plan(db, plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


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


@router.post("/sessions/{session_id}/complete", status_code=204)
def complete_session(session: OwnedPlannedSession, db: DbSession) -> None:
    """Tick a session off by hand.

    One of three routes to the same write — the other two are the auto-match from
    a synced activity and telling the coach in conversation. Strava never sees
    the gym, so the tap is not a fallback for the matcher failing; it is how a
    whole class of session gets recorded at all.

    204 rather than the updated session, deliberately. A tick changes the week's
    headline, its done count and its discipline mix, so handing back one session
    would invite a client to patch state it cannot correctly patch — and it also
    means an off-vocabulary row (the plan is LLM-written, so a stored row can
    carry a discipline outside the closed set) cannot make a SUCCESSFUL write
    answer with a serialization error.
    """
    completion.complete_planned_session(db, session, source=completion.MANUAL)


@router.delete("/sessions/{session_id}/complete", status_code=204)
def uncomplete_session(session: OwnedPlannedSession, db: DbSession) -> None:
    """Untick. The runner is allowed to be wrong about their own week."""
    completion.clear_completion(db, session)


@router.post("/sessions/{session_id}/dismiss", status_code=204)
def dismiss_session(session: OwnedPlannedSession, db: DbSession) -> None:
    """Decline a SUGGESTION.

    Only a suggestion. Declining something the runner agreed to is a plan change,
    and plan changes go through the coach — otherwise the schedule quietly
    becomes a to-do list the runner edits, which is the one thing it is not.
    """
    try:
        completion.dismiss_planned_session(db, session)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
