"""The runner's plan, as the coach receives it (#830).

The coach reads the past and the present well; what it lacked was the future.
With the plan in the pack it can read a session against what it was FOR — "you
hit the 800s" rather than "you ran with some fast bits" — and say what a session
sets up rather than only what it was.

Two questions decide everything in here (the project's north star).

**What does a good coach actually need?** For a report about ONE run: what that
run was meant to be, what is still to come this week, and the spacing rules, so
its advice does not contradict the plan it wrote itself. Not the twelve-week
horizon — that is the runner's screen, and a report about Tuesday does not need
October. Not the sketched weeks, not the load numbers: the coach is given the
prescription, and the measured record of what happened is already in front of it.

**Could an LLM misread it?** Yes, in one specific and predictable way: handed a
plan and a result, a model reaches for a COMPLIANCE VERDICT. "You missed two
sessions." That is precisely the nagging the runner-memory redesign (ADR 0025)
was built to remove, and it is why this section carries no adherence count, no
percentage and no hit/miss label. It states intent, and the coach compares.

So every field here is what was ASKED FOR, never what happened. `done_this_week`
is the one count of completions and it exists to stop the coach telling a runner
who has done everything that they have work left. There is deliberately no
per-session done/not-done flag: that shape is a scorecard, and a scorecard is
what gets read out.
"""

import logging
from datetime import date, timedelta
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.schemas.coach_context import (
    PlannedForContext,
    ScheduleContext,
    UpcomingSessionContext,
)
from app.services.schedule import store
from app.services.schedule.completion import find_matching_session
from app.services.schedule.placement import (
    WEEK_LENGTH_DAYS,
    derive_placement,
    effective_window,
    session_status,
)
from app.services.schedule.rule_text import describe_rule
from app.services.weeks import resolve_week_start, week_start

logger = logging.getLogger(__name__)

# Whether this module runs at all is decided elsewhere. `COACH_SCHEDULE_ENABLED`
# is declared on the signal in `signal_registry` and applied ONCE by
# `read_time_signals.gather`, so there is deliberately no `if not settings...`
# here — #800 removed exactly that duplication from the memory and
# training-history builders, and a switch with two owners is a switch that can
# disagree with itself. Off, `gather` never calls this and the section drops
# byte-stably; the runner's schedule screen is untouched (that is the separate
# `SCHEDULE_ENABLED`).

# How many upcoming sessions ride the pack. A week rarely holds more, and a coach
# writing about one run does not need an exhaustive list to say what it sets up.
MAX_UPCOMING = 8
MAX_RULES = 6


def _target(session: Any) -> Optional[str]:
    """The prescription in a unit a coach would speak.

    Never `effort_score`. It is a modelled cumulative number that reads as an
    intensity verdict (#168), and it is not what anyone was asked to do — the
    same rule the runner's own screen follows.
    """
    structure = session.structure or {}
    reps = structure.get("reps_planned")
    if reps:
        distance = structure.get("rep_distance_m")
        rest = structure.get("rest_s")
        text = f"{int(reps)} x {int(distance)} m" if distance else f"{int(reps)} reps"
        return f"{text} off {int(rest)} s" if rest else text
    if session.target_distance_m:
        return f"{session.target_distance_m / 1000:.1f} km"
    if session.target_duration_s:
        return f"{int(session.target_duration_s // 60)} min"
    return None


def _when(session: Any, today: date, starts_on: int) -> str:
    """When it may happen, in the runner's own terms."""
    placement = derive_placement(session.window_start, session.window_end, starts_on)
    if placement == "pinned":
        return session.window_start.strftime("%a")
    if placement == "week":
        return "any day"
    window = effective_window(session.window_start, session.window_end, today) or (
        session.window_start,
        session.window_end,
    )
    start, end = window
    if start == end:
        return start.strftime("%a")
    return f"{start.strftime('%a')}-{end.strftime('%a')}"


def _week_view(
    db: Session,
    user_id: Any,
    plan: Any,
    today: date,
    starts_on: int,
    *,
    exclude_session_id: Any = None,
) -> tuple[List[UpcomingSessionContext], int, int]:
    """This week's committed sessions, as intent rather than as a scorecard.

    Shared by the report pack and the conversation so both read one week the same
    way. `done` is a COUNT and there is deliberately no per-session done flag —
    that shape is a scorecard, and a scorecard is what gets read out.
    """
    start = week_start(today, starts_on)
    end = start + timedelta(days=WEEK_LENGTH_DAYS - 1)
    rows = store.sessions_in_range(db, user_id, start, end, plan_id=plan.id)

    upcoming: List[UpcomingSessionContext] = []
    committed = 0
    done = 0
    for row in rows:
        if row.commitment != "committed":
            continue
        committed += 1
        status = session_status(row, today)
        if status == "done":
            done += 1
            continue
        # The session this activity IS gets described once, as what it was for.
        # Listing it again under "still to come" would have the coach telling the
        # runner to go and do the run it is writing about.
        if exclude_session_id is not None and row.id == exclude_session_id:
            continue
        if status != "upcoming" or row.intent == "rest":
            continue
        if len(upcoming) < MAX_UPCOMING:
            upcoming.append(
                UpcomingSessionContext(
                    when=_when(row, today, starts_on),
                    title=row.title,
                    intent=row.intent,
                    discipline=row.discipline,
                    target=_target(row),
                )
            )
    return upcoming, committed, done


def build_schedule_context(
    db: Session, activity: Any, as_of: Any = None
) -> Optional[ScheduleContext]:
    """The plan section, or None when the runner has no plan.

    None rather than an empty shell: a runner with no schedule should leave the
    pack byte-identical to what it was before this signal existed, so the coach
    is never handed an empty plan to reason about.
    """
    user_id = activity.user_id
    plan = store.get_active_plan(db, user_id)
    if plan is None:
        return None

    profile = getattr(getattr(activity, "user", None), "profile", None)
    starts_on = resolve_week_start(profile)
    today = getattr(activity, "local_start", None)
    today = today.date() if today is not None else date.today()

    planned_for = None
    matched = find_matching_session(db, activity)
    if matched is not None:
        planned_for = PlannedForContext(
            title=matched.title,
            intent=matched.intent,
            discipline=matched.discipline,
            target=_target(matched),
            detail=matched.detail,
        )

    upcoming, committed, done = _week_view(
        db,
        user_id,
        plan,
        today,
        starts_on,
        exclude_session_id=matched.id if matched is not None else None,
    )

    # The DERIVED statement, not the coach's own `label` (#844) — the same text
    # the runner's schedule screen shows them. A label is written by the coach
    # and tied to nothing, so it can describe a rule its own predicate does not
    # enforce (a live plan's label promised "easy cross-training" against a
    # predicate forbidding exactly that). Feeding the label here would leave the
    # runner reading one rule and the coach reasoning from another, which is
    # worse than the two being wrong together.
    rules = [describe_rule(rule) for rule in store.plan_rules(plan)][:MAX_RULES]

    if not (planned_for or upcoming or rules):
        # Nothing to say. An empty section is tokens spent on silence.
        return None

    return ScheduleContext(
        planned_for_this_activity=planned_for,
        still_to_come_this_week=upcoming,
        rules_in_play=rules,
        committed_this_week=committed,
        done_this_week=done,
    )


def build_thread_schedule(
    db: Session, user: Any, *, today: Optional[date] = None
) -> Optional[dict]:
    """The runner's plan as the CONVERSATION needs it (#856).

    The report's section is anchored to one finished activity and answers "what
    was this run for". A conversation is anchored to the runner and to now, so
    this answers a different question: is there a plan, what does it still ask of
    this week, and how far does it run. Without it the coach was blind to the
    schedule entirely — which is how it came to send a runner standing on the
    Schedule screen away to "your schedule app".

    Same two disciplines as the pack section: the prescription is named in units
    a coach speaks (never `effort_score`), and there is no per-session hit/miss
    flag, because handing a model a plan and a result invites the compliance
    verdict ADR 0025 exists to remove.

    Returns None when the runner has no active plan — which is itself worth
    knowing, so the caller says so in words rather than passing an empty shell.
    """
    plan = store.get_active_plan(db, user.id)
    if plan is None:
        return None

    starts_on = resolve_week_start(getattr(user, "profile", None))
    today = today or date.today()
    upcoming, committed, done = _week_view(db, user.id, plan, today, starts_on)

    return {
        "has_plan": True,
        "runs_through": plan.horizon_end.isoformat() if plan.horizon_end else None,
        "still_to_come_this_week": [item.model_dump() for item in upcoming],
        "committed_this_week": committed,
        "done_this_week": done,
        "rules_in_play": [rule.label for rule in store.plan_rules(plan)][:MAX_RULES],
    }
