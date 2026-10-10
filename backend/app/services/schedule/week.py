"""The week read — one builder, planned and free (#830).

Free mode is not a second screen and not an empty state. It is this same read
with no active plan: the week fills in as the runner trains, measured against
their own typical week rather than a target, and the coach's suggestions (if any)
sit alongside without committing anyone to anything.

Nothing here computes a training total of its own. Actuals come from
`activity_facts` (the one owner-scoped fact stream), the week boundary from
`weeks.py` (parameterised by the runner's `week_starts_on`), and "typical" from
`coach/volume.py`'s existing builder. The schedule must not become a second
opinion about a week the Trends page has already answered.

One thing deliberately dropped from the read: a SUGGESTION whose window has
passed unacted-on, and a suggestion the runner dismissed. "A suggestion you
ignore leaves no trace and generates no follow-up — otherwise free mode becomes a
plan with extra guilt." A committed session that was missed is kept, because
missing something you agreed to is exactly what the coach should see.
"""

import logging
from datetime import date, timedelta
from typing import Any, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.schedule import (
    DisciplineLoad,
    LoggedActivityRead,
    PlannedSessionRead,
    RuleViolation,
    DroppedSession,
    RecommendedDay,
    RecommendedItem,
    RunningVsNorm,
    SessionAlternative,
    WeekRecommendationRead,
    ScheduleWeekRead,
    SpacingRuleRead,
    WeekHeadline,
)
from app.services.activity_facts import (
    BASELINE_WEEKS,
    facts_in_window,
    is_run,
    query_facts,
)
from app.services.coach.volume import build_training_volume
from app.services.schedule import store
from app.services.schedule.disciplines import discipline_for_fact
from app.services.schedule.placement import (
    WEEK_LENGTH_DAYS,
    PlacedSession,
    candidate_days,
    derive_placement,
    effective_window,
    has_narrowed,
    session_status,
)
from app.services.schedule.norms import running_vs_norm
from app.services.schedule.rule_text import describe_rule
from app.services.schedule import recommend_store
from app.services.schedule.preferences import describe_preference, plan_preferences
from app.services.schedule.recommend import alternative_notes, recommend_week
from app.services.schedule.rules import check_rules, open_days
from app.services.schedule.runner_rules import with_runner_rules
from app.services.weeks import days_into_week, resolve_week_start, week_start

# The disciplines a week is reported in, in a fixed order so the mix bar does not
# reshuffle between reads.
logger = logging.getLogger(__name__)

DISCIPLINE_ORDER = ("run", "walk", "bike", "strength", "row", "other")

# How far back the norm's baseline needs to see. `build_training_volume` computes
# its 12-week baseline over history BEFORE the current 7 days, so the fetch has to
# reach past both.
_NORM_LOOKBACK_DAYS = BASELINE_WEEKS * 7 + 14


def _alternatives(session: Any) -> List[SessionAlternative]:
    """The session's stored alternatives, any off-shape one dropped (#1082).

    One bad option must not take the whole session off the week, which is what
    letting its `ValidationError` reach `PlannedSessionRead` would do.
    """
    kept: List[SessionAlternative] = []
    for raw in getattr(session, "alternatives", None) or []:
        try:
            kept.append(SessionAlternative.model_validate(raw))
        except ValidationError:
            logger.warning("schedule: dropping off-shape alternative on session %s", session.id)
    return kept


def _to_session_read(
    session: Any, today: date, starts_on: int
) -> Optional[PlannedSessionRead]:
    """One session as the API shape, or None if the row is off-vocabulary.

    The plan is LLM-written, so a row can carry a discipline or intent outside
    the closed set despite the schema at the boundary — a stored row predating a
    vocabulary change would do it too. Dropping that row with a warning is the
    same containment the JSON columns already get in `store.py`; letting the
    `ValidationError` escape would take down the whole week over one bad card.
    """
    effective = effective_window(session.window_start, session.window_end, today)
    try:
        return PlannedSessionRead(
            id=session.id,
            window_start=session.window_start,
            window_end=session.window_end,
            placement=derive_placement(
                session.window_start, session.window_end, starts_on
            ),
            effective_window_start=effective[0] if effective else None,
            effective_window_end=effective[1] if effective else None,
            has_narrowed=has_narrowed(
                session.window_start, session.window_end, today
            ),
            intent=session.intent,
            discipline=session.discipline,
            commitment=session.commitment,
            status=session_status(session, today),
            title=session.title,
            detail=session.detail,
            target_distance_m=session.target_distance_m,
            target_duration_s=session.target_duration_s,
            target_effort_score=session.target_effort_score,
            structure=session.structure,
            completed_at=session.completed_at,
            completed_activity_id=session.completed_activity_id,
            completion_source=session.completion_source,
            dismissed_at=session.dismissed_at,
            alternatives=_alternatives(session),
            done_option=session.done_option,
            alternative_notes=alternative_notes(session, _alternatives(session)),
        )
    except ValidationError:
        logger.warning(
            "schedule: dropping off-vocabulary planned session %s "
            "(intent=%r discipline=%r commitment=%r)",
            session.id,
            session.intent,
            session.discipline,
            session.commitment,
        )
        return None


def _done_on(row: Any, fact_day_by_activity: dict) -> Optional[date]:
    """The day a done session used up, kept inside its own window (#1081).

    A session matched to an activity was done on that activity's day. One ticked
    by hand carries only the moment of the tick, which is the best the app knows:
    a walk ticked on Thursday for Tuesday counts on Thursday unless that is
    outside its window, where it is held to the nearest day it could have been.
    """
    if getattr(row, "completed_at", None) is None:
        return None
    day = fact_day_by_activity.get(getattr(row, "completed_activity_id", None))
    if day is None:
        day = row.completed_at.date()
    return min(max(day, row.window_start), row.window_end)


def _done_intent(row: Any) -> str:
    """The intent of the option actually done (#1082): an easy row done in place
    of strength is held to the rules as easy, not as the strength it replaced."""
    option = getattr(row, "done_option", None) or 0
    alternatives = getattr(row, "alternatives", None) or []
    if 0 < option <= len(alternatives) and isinstance(alternatives[option - 1], dict):
        return alternatives[option - 1].get("intent") or row.intent
    return row.intent


def _recommend(db, user, plan, start, today, is_current, committed_open, rules, fixed):
    """The recommended week (#1082), stored for the current week so the next read
    can keep sessions where they were and say what moved."""
    row = recommend_store.load(db, user.id, start, plan.id) if is_current else None
    rec = recommend_week(
        committed_open,
        rules,
        plan_preferences(plan),
        today,
        fixed=fixed,
        previous=recommend_store.previous_days(row),
    )
    titles = {str(r.id): r.title for r in committed_open}
    if is_current:
        row = recommend_store.save(
            db, row, user_id=user.id, week_start=start, plan_id=plan.id,
            rec=rec, titles=titles, today=today,
        )
    days: dict = {}
    for placement in rec.placements.values():
        days.setdefault(placement.day, []).append(placement)
    return WeekRecommendationRead(
        days=[
            RecommendedDay(
                day=day,
                items=[
                    RecommendedItem(session_id=p.session_id, order=p.order, reason=p.reason)
                    for p in sorted(items, key=lambda p: (p.order is None, p.order or 0, titles.get(str(p.session_id), "")))
                ],
            )
            for day, items in sorted(days.items())
        ],
        dropped=[DroppedSession(session_id=sid, reason=why) for sid, why in rec.dropped.items()],
        changes=[
            text
            for text in (recommend_store.describe_change(c) for c in (row.changes if row else []))
            if text
        ],
        preferences=[describe_preference(p) for p in plan_preferences(plan)],
    )


def _to_logged_read(fact: Any) -> LoggedActivityRead:
    return LoggedActivityRead(
        activity_id=getattr(fact, "activity_id", None),
        local_date=fact.local_date,
        activity_type=fact.activity_type or "",
        discipline=discipline_for_fact(fact),
        distance_m=fact.distance_m or 0.0,
        moving_time_s=int(fact.moving_time_s or 0),
        effort_score=fact.effort_score or 0.0,
    )


def _by_discipline(
    sessions: List[PlannedSessionRead], logged: List[LoggedActivityRead]
) -> List[DisciplineLoad]:
    """Planned and logged load per discipline, in `effort_score`.

    Both halves ride one row because the mix bar shows them together: the solid
    part is what is done, the faded part what is still to come.
    """
    rows = {name: DisciplineLoad(discipline=name) for name in DISCIPLINE_ORDER}
    for session in sessions:
        row = rows[session.discipline]
        row.planned_sessions += 1
        row.planned_effort_score += session.target_effort_score or 0.0
    for entry in logged:
        row = rows[entry.discipline]
        row.logged_sessions += 1
        row.logged_effort_score += entry.effort_score
    # Only disciplines that actually appear: an empty row would draw an empty
    # segment in the mix bar and read as a discipline the runner trains.
    return [
        rows[name]
        for name in DISCIPLINE_ORDER
        if rows[name].planned_sessions or rows[name].logged_sessions
    ]


def _is_visible(session: Any, status: str) -> bool:
    """A dismissed or lapsed SUGGESTION leaves no trace."""
    if session.commitment != "suggested":
        return True
    return status not in ("dismissed", "missed")


def build_week(
    db: Session,
    user: User,
    *,
    target_week: Optional[date] = None,
    today: Optional[date] = None,
) -> ScheduleWeekRead:
    """The runner's week: what is planned, what is logged, and how it compares."""
    today = today or date.today()
    profile = getattr(user, "profile", None)
    starts_on = resolve_week_start(profile)

    anchor = target_week or today
    start = week_start(anchor, starts_on)
    end = start + timedelta(days=WEEK_LENGTH_DAYS - 1)
    is_current = start == week_start(today, starts_on)

    plan = store.get_active_plan(db, user.id)
    # Scoped to the ACTIVE plan. A superseded plan's sessions are history, not
    # this week's work: reading them unscoped would show a replaced plan's
    # sessions under a headline computed from the current one, and with no active
    # plan at all it would show sessions while reporting `has_plan` false.
    rows = (
        store.sessions_in_range(db, user.id, start, end, plan_id=plan.id)
        if plan is not None
        else []
    )

    sessions: List[PlannedSessionRead] = []
    for row in rows:
        status = session_status(row, today)
        if not _is_visible(row, status):
            continue
        read = _to_session_read(row, today, starts_on)
        if read is not None:
            sessions.append(read)

    # One fetch covers both the week's actuals and the norm's baseline; the week
    # is then narrowed out of it rather than queried again.
    fetch_from = min(start, today - timedelta(days=_NORM_LOOKBACK_DAYS))
    fetch_to = max(end, today) + timedelta(days=1)
    facts = query_facts(db, fetch_from, fetch_to, user_id=user.id)
    week_facts = facts_in_window(facts, start, end + timedelta(days=1))

    logged = [_to_logged_read(fact) for fact in week_facts]

    committed = [s for s in sessions if s.commitment == "committed"]
    headline = WeekHeadline(
        planned_running_distance_m=(
            # Read off the session the client is handed, not recomputed from
            # the row beside it (#887). `PlannedSessionRead.planned_distance_m`
            # is `planned_distance.planned_distance_m` either way, so a run
            # sized by rep structure counts its work instead of summing as zero
            # (#876) — but summing the carried value makes the headline and the
            # card arithmetically the same number rather than two readings that
            # happen to agree.
            sum(
                s.planned_distance_m
                for s in committed
                if s.discipline == "run"
            )
            if plan is not None
            else None
        ),
        logged_running_distance_m=sum(
            (f.distance_m or 0.0) for f in week_facts if is_run(f)
        ),
        # Both counts exclude a prescribed rest for the same reason: "3 of 7
        # sessions" is a count of work. Counting rest on only one side of the
        # pair produced "0 planned, 1 done" for a week whose rest card was ticked.
        planned_sessions=len([s for s in committed if s.intent != "rest"]),
        done_sessions=len(
            [s for s in committed if s.status == "done" and s.intent != "rest"]
        ),
    )

    # The runner's own rules (#1080) are checked and shown beside the plan's.
    rules = with_runner_rules(store.plan_rules(plan), profile)
    # The runner-facing STATEMENT is derived here, from kind + arguments, never
    # from the coach's own `label` (#844) — see rule_text.py.
    rules_read = [
        SpacingRuleRead(**rule.model_dump(), statement=describe_rule(rule))
        for rule in rules
    ]
    # Only sessions still looking for a day constrain anything. A session already
    # done, dismissed or missed cannot be moved, so asking whether it fits would
    # report a failure the runner can do nothing about.
    open_rows = [
        row
        for row in rows
        if session_status(row, today) == "upcoming" and candidate_days(row, today)
    ]
    # #1081: a done session cannot move, but it still occupies the day it was
    # done, so it takes part in every rule without being searched over.
    fact_day_by_activity = {
        getattr(fact, "activity_id", None): fact.local_date for fact in week_facts
    }
    done_on_by_id = {}
    fixed: List[PlacedSession] = []
    for row in rows:
        if session_status(row, today) != "done":
            continue
        day = _done_on(row, fact_day_by_activity)
        done_on_by_id[row.id] = day
        fixed.append(PlacedSession(session_id=row.id, intent=_done_intent(row), day=day))
    _, raw_violations = check_rules(open_rows, rules, today, fixed=fixed)
    violations = [RuleViolation(**v) for v in raw_violations]

    # #1081: the days each floating session can still go on. Asked of the same
    # search the violations come from, so an option shown is never one the
    # checker would reject. None means the search ran out of budget, and the
    # client falls back to the effective window.
    # Suggestions are left out: an offer the runner may decline must not take a
    # day away from a session they agreed to.
    committed_open = [row for row in open_rows if row.commitment == "committed"]
    # #1083: an activity that matched no session is an extra. It still used up
    # one of its day's activities under the runner's limit, so the day's
    # remaining options and the recommendation both see it.
    credited = {row.completed_activity_id for row in rows if row.completed_activity_id}
    uncredited = [
        fact
        for fact in week_facts
        if getattr(fact, "activity_id", None) not in credited and fact.local_date <= today
    ]
    # A session ticked by hand carries no activity, but the runner may have
    # recorded it too. Each such session claims one recorded activity of its
    # discipline on its day, so the same walk is not counted twice.
    for row in rows:
        if session_status(row, today) != "done" or row.completed_activity_id:
            continue
        day = done_on_by_id.get(row.id)
        claim = next(
            (
                f
                for f in uncredited
                if f.local_date == day and discipline_for_fact(f) == row.discipline
            ),
            None,
        )
        if claim is not None:
            uncredited.remove(claim)
    extras = [
        PlacedSession(session_id=f"extra-{fact.activity_id}", intent="easy", day=fact.local_date)
        for fact in uncredited
    ]
    open_by_id = (
        open_days(committed_open, rules, today, fixed=fixed + extras) if committed_open else {}
    )
    recommendation = (
        _recommend(db, user, plan, start, today, is_current, committed_open, rules, fixed + extras)
        if plan is not None and end >= today
        else None
    )
    sessions = [
        s.model_copy(
            update={
                "done_on": done_on_by_id.get(s.id),
                "open_days": (
                    open_by_id.get(s.id)
                    if open_by_id is not None
                    and s.placement != "pinned"
                    and s.status == "upcoming"
                    and s.commitment == "committed"
                    else None
                ),
            }
        )
        for s in sessions
    ]

    norm = None
    running_norm = None
    if is_current:
        volume = build_training_volume(facts, today, starts_on)
        norm = volume.calendar_week.metrics
        elapsed = days_into_week(today, starts_on)
        raw = running_vs_norm(facts, week_facts, today, days_elapsed=elapsed)
        running_norm = RunningVsNorm(**raw) if raw else None

    return ScheduleWeekRead(
        week_start=start,
        week_end=end,
        is_current_week=is_current,
        has_plan=plan is not None,
        plan_id=plan.id if plan is not None else None,
        headline=headline,
        sessions=sessions,
        logged=logged,
        by_discipline=_by_discipline(sessions, logged),
        rules=rules_read,
        violations=violations,
        recommendation=recommendation,
        norm=norm,
        running_norm=running_norm,
    )


def session_read(user: User, session: Any, *, today: Optional[date] = None):
    """One session in the API shape, for the endpoints that act on a single one.

    Shares `_to_session_read` with the week builder so a session tapped done
    renders exactly as the same session does inside the week — including its
    derived placement, effective window and status.
    """
    today = today or date.today()
    starts_on = resolve_week_start(getattr(user, "profile", None))
    return _to_session_read(session, today, starts_on)
