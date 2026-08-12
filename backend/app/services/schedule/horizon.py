"""The multi-week horizon: the shape of the block, not the next seven days (#830).

Concrete for about three weeks, shape only beyond — a settled design decision.
Which is which is DERIVED, never stored: a week with real `planned_sessions` rows
is `planned`, a week with only a `PlannedWeekShape` is sketched. A stored flag
could claim a week was planned after its sessions were removed; a derivation
cannot.

Bar length is LOAD (`effort_score`), because a strength session and a turbo ride
have no distance and would vanish from a three-month view of holistic training.
Running km rides alongside as a plain number — exact, rather than estimated off a
bar — and the gap between a long bar and a small number is the week the runner
cross-trained.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.schedule import GoalRaceRead, HorizonWeek, ScheduleHorizonRead
from app.services.schedule import store
from app.services.schedule.placement import WEEK_LENGTH_DAYS
from app.services.weeks import resolve_week_start, week_start

DEFAULT_HORIZON_WEEKS = 12
MAX_HORIZON_WEEKS = 52


def _mix(totals: Dict[str, float]) -> Dict[str, float]:
    """Absolute per-key load -> shares of the whole, rounded.

    Shares rather than absolutes so a mix can never contradict the total it is a
    mix of. An all-zero week yields an empty mix rather than a divide-by-zero or
    a fake even split.
    """
    total = sum(totals.values())
    if total <= 0:
        return {}
    return {
        key: round(value / total, 4)
        for key, value in totals.items()
        if value > 0
    }


def _week_from_sessions(sessions: List[Any]) -> Dict[str, Any]:
    by_discipline: Dict[str, float] = {}
    by_intent: Dict[str, float] = {}
    running_distance = 0.0
    load = 0.0
    for session in sessions:
        if session.commitment != "committed":
            continue
        effort = session.target_effort_score or 0.0
        load += effort
        by_discipline[session.discipline] = (
            by_discipline.get(session.discipline, 0.0) + effort
        )
        by_intent[session.intent] = by_intent.get(session.intent, 0.0) + effort
        if session.discipline == "run":
            running_distance += session.target_distance_m or 0.0
    return {
        "running_distance_m": running_distance,
        "effort_score": load,
        "discipline_mix": _mix(by_discipline),
        "intent_mix": _mix(by_intent),
    }


def _last_covered_week(
    plan: Optional[Any],
    shapes: Dict[date, Any],
    rows: List[Any],
    starts_on: int,
) -> Optional[date]:
    """The last week the plan covers, concrete or sketched — the boundary #842's
    `beyond_plan` reads against.

    `horizon_end` (the last DAY of the last week the plan mentions, written by
    `draft.py`) is a FLOOR on the plan's reach, never a ceiling: it is always
    taken together with the latest week among the plan's own week shapes and its
    COMMITTED sessions (a suggestion nobody agreed to does not extend the plan's
    reach, the same reading `planned` above already applies), and the later of
    the two wins (#848). A `TrainingPlan.horizon_end` that understates what was
    actually written — the writer disagreeing with itself, a correction, a
    directly-constructed row — must never let a week holding real content read
    as `beyond_plan`: the view downstream draws a single "Plan ends here"
    divider on the assumption that once a week is `beyond_plan` every later
    week is too, so this function is the one place that assumption is made
    true rather than merely hoped true. `rows` is `sessions_in_range`'s result,
    bounded to the requested window, so this can still undercount a plan whose
    committed sessions run past the window asked for; that is an accepted
    limitation of the safety net, not of `horizon_end` itself.

    None (no plan, or a plan stating nothing at all) means there is nothing to
    be beyond — every week then reads `empty`, never `beyond_plan`.
    """
    if plan is None:
        return None
    candidates: List[date] = []
    if plan.horizon_end is not None:
        candidates.append(week_start(plan.horizon_end, starts_on))
    candidates.extend(shapes.keys())
    candidates.extend(
        week_start(row.window_start, starts_on)
        for row in rows
        if row.commitment == "committed"
    )
    return max(candidates) if candidates else None


def build_horizon(
    db: Session,
    user: User,
    *,
    weeks: int = DEFAULT_HORIZON_WEEKS,
    today: Optional[date] = None,
) -> ScheduleHorizonRead:
    today = today or date.today()
    weeks = max(1, min(weeks, MAX_HORIZON_WEEKS))

    profile = getattr(user, "profile", None)
    starts_on = resolve_week_start(profile)
    first_week = week_start(today, starts_on)
    last_week = first_week + timedelta(days=WEEK_LENGTH_DAYS * (weeks - 1))
    span_end = last_week + timedelta(days=WEEK_LENGTH_DAYS - 1)

    plan = store.get_active_plan(db, user.id)
    # Active plan only — a superseded plan's sessions are history, and showing
    # them would draw a horizon out of two different plans at once.
    rows = (
        store.sessions_in_range(db, user.id, first_week, span_end, plan_id=plan.id)
        if plan is not None
        else []
    )

    sessions_by_week: Dict[date, List[Any]] = {}
    for row in rows:
        key = week_start(row.window_start, starts_on)
        sessions_by_week.setdefault(key, []).append(row)

    shapes = {shape.week_start: shape for shape in store.plan_week_shapes(plan)}
    last_covered_week = _last_covered_week(plan, shapes, rows, starts_on)

    out: List[HorizonWeek] = []
    for index in range(weeks):
        current = first_week + timedelta(days=WEEK_LENGTH_DAYS * index)
        week_sessions = sessions_by_week.get(current, [])
        shape = shapes.get(current)

        # A week is PLANNED when it holds something the runner committed to. A
        # week carrying only suggestions has no plan in it — reporting it as
        # planned would draw a solid tick and a zero-length bar, claiming a week
        # was written when nothing was.
        if any(s.commitment == "committed" for s in week_sessions):
            derived = _week_from_sessions(week_sessions)
            out.append(
                HorizonWeek(
                    week_start=current,
                    phase=shape.phase if shape else None,
                    planned=True,
                    coverage="planned",
                    is_current=current == first_week,
                    **derived,
                )
            )
        elif shape is not None:
            out.append(
                HorizonWeek(
                    week_start=current,
                    phase=shape.phase,
                    planned=False,
                    coverage="sketched",
                    is_current=current == first_week,
                    running_distance_m=shape.target_running_distance_m,
                    effort_score=shape.target_effort_score,
                    discipline_mix=shape.discipline_mix,
                    intent_mix=shape.intent_mix,
                )
            )
        else:
            # A week the plan says nothing about is reported as empty rather than
            # omitted, so the horizon stays a continuous run of weeks and a gap
            # reads as a gap instead of as a shorter block. #842: that used to be
            # the whole story, but a week past the plan's own reach is a
            # different thing from a genuine interior gap — only the latter is
            # actually a week the plan "says nothing about"; the former the plan
            # never had a chance to.
            coverage = (
                "beyond_plan"
                if last_covered_week is not None and current > last_covered_week
                else "empty"
            )
            out.append(
                HorizonWeek(
                    week_start=current,
                    phase=None,
                    planned=False,
                    coverage=coverage,
                    is_current=current == first_week,
                )
            )

    loads = [w.effort_score for w in out if w.effort_score]
    races = store.list_goal_races(db, user.id, on_or_after=first_week)

    return ScheduleHorizonRead(
        weeks=out,
        races=[
            GoalRaceRead.model_validate(race)
            for race in races
            if race.race_date <= span_end
        ],
        has_plan=plan is not None,
        peak_effort_score=max(loads) if loads else None,
    )
