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
The one deliberate exception is `next_week_committed` (#943): a fixed, capped,
one-week-further reach for COMMITTED sessions only, added because the coach
already reaches that far on its own — "next week's long run" — and reaching
forward is good coaching. Withholding the fact does not stop the reach; it only
makes the reach invent one. This is still not the horizon: it is one more week,
never twelve.

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
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.schemas.coach_context import (
    PlannedForContext,
    ScheduleContext,
    UpcomingSessionContext,
)
from app.services.schedule import store
from app.services.schedule.completion import find_matching_session
from app.services.schedule.planned_distance import planned_distance_m
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


def _km(metres: float, unmeasured_runs: int = 0) -> Optional[str]:
    """The week's planned running, as the runner's own screen writes it.

    Zero is not a number to hand over: a week whose sessions state no distance
    has no planned running total, and "0.00 km" would read as a week off.

    A run that states no distance — "4 reps off 90 s", a real and legal
    prescription — contributes nothing to this, so the total can be a real number
    that is nonetheless not the whole week. Left bare, that is the north star's
    second question failing: whatever is ambiguous is eventually read wrong, and
    a coach reading 17 km as the complete week would plan the next one against a
    figure short by a session. So the total says when it is partial rather than
    being withheld for it.
    """
    if not metres:
        return None
    total = f"{metres / 1000:.2f} km"
    if not unmeasured_runs:
        return total
    runs = "run" if unmeasured_runs == 1 else "runs"
    return f"{total}, plus {unmeasured_runs} {runs} whose distance was not stated"


def describe_written_ago(plan: Any, now: Optional[datetime] = None) -> Optional[str]:
    """How long ago this plan was written, in the terms a person would use.

    Shared by the runner's confirm card and the coach's own read of the schedule
    (#883), because both need the same fact and a plan cannot be two ages. A
    runner asked "Is it added?", was offered a second card, and confirmed it —
    replacing the plan they had accepted ninety seconds earlier with a differently
    generated one. Drafting is not deterministic, so that is a different plan, and
    the card said only "your current one": true, and silent about the part that
    would have stopped them.
    """
    written = getattr(plan, "generated_at", None) or getattr(plan, "created_at", None)
    if written is None:
        return None
    now = now or datetime.now(timezone.utc)
    # SQLite hands back naive datetimes where Postgres does not, and subtracting
    # across the two raises rather than being wrong quietly.
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    minutes = int((now - written).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


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
        prescription = f"{text} off {int(rest)} s" if rest else text
        # The session's own distance leads, exactly as it does on the runner's
        # card (#880). A rep session states no total, so this used to be the
        # prescription alone — and a runner looking at "4.50 km" and asking why
        # their week read 25 was asking about a number the coach had never been
        # shown. It invented a mechanism for it, then diagnosed the app. The
        # prescription stays: it is what they actually go out and do.
        total = planned_distance_m(session)
        return f"{total / 1000:.2f} km ({prescription})" if total else prescription
    if session.target_distance_m:
        return f"{session.target_distance_m / 1000:.1f} km"
    if session.target_duration_s:
        return f"{int(session.target_duration_s // 60)} min"
    return None


def _when(session: Any, today: date, starts_on: int, *, next_week: bool = False) -> str:
    """When it may happen, in the runner's own terms.

    `next_week` disambiguates a session drawn from the week AFTER the one `today`
    sits in (#943): its own weekday abbreviation ("Sat") reads identically to a
    session from the current week, and a coach naming a distance against the
    wrong Saturday is exactly how #943 shipped.
    """
    placement = derive_placement(session.window_start, session.window_end, starts_on)
    if placement == "week":
        return "next week, any day" if next_week else "any day"
    if placement == "pinned":
        day = session.window_start.strftime("%a")
        return f"next {day}" if next_week else day
    window = effective_window(session.window_start, session.window_end, today) or (
        session.window_start,
        session.window_end,
    )
    start, end = window
    if start == end:
        day = start.strftime("%a")
        return f"next {day}" if next_week else day
    span = f"{start.strftime('%a')}-{end.strftime('%a')}"
    return f"next {span}" if next_week else span


def _week_view(
    db: Session,
    user_id: Any,
    plan: Any,
    today: date,
    starts_on: int,
    *,
    exclude_session_id: Any = None,
) -> tuple[List[UpcomingSessionContext], int, int, Optional[str]]:
    """This week's committed sessions, as intent rather than as a scorecard.

    Shared by the report pack and the conversation so both read one week the same
    way. `done` is a COUNT and there is deliberately no per-session done flag —
    that shape is a scorecard, and a scorecard is what gets read out.

    The running total is the fourth return (#880). It is the number the runner's
    own week headline shows them, summed here through the same
    `planned_distance_m` that produced it, so the coach and the screen cannot
    hold two opinions about one week. Only what was PLANNED: what was actually
    run is reachable through the coach's own tools, and pairing the two here is
    the compliance verdict this module exists not to hand over.
    """
    start = week_start(today, starts_on)
    end = start + timedelta(days=WEEK_LENGTH_DAYS - 1)
    rows = store.sessions_in_range(db, user_id, start, end, plan_id=plan.id)

    upcoming: List[UpcomingSessionContext] = []
    committed = 0
    done = 0
    planned_running_m = 0.0
    unmeasured_runs = 0
    for row in rows:
        if row.commitment != "committed":
            continue
        committed += 1
        if row.discipline == "run":
            distance = planned_distance_m(row)
            planned_running_m += distance
            if not distance:
                unmeasured_runs += 1
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
                    session_id=str(row.id),
                    when=_when(row, today, starts_on),
                    title=row.title,
                    intent=row.intent,
                    discipline=row.discipline,
                    target=_target(row),
                    detail=row.detail,
                )
            )
    return upcoming, committed, done, _km(planned_running_m, unmeasured_runs)


def _next_week_committed(
    db: Session,
    user_id: Any,
    plan: Any,
    today: date,
    starts_on: int,
) -> List[UpcomingSessionContext]:
    """The bounded look one week past `_week_view` (#943).

    Not the horizon this module exists to withhold — a fixed one-week-further
    reach, capped exactly like this week's list. COMMITTED sessions only: a
    suggestion the runner may still dismiss is not something to hand the coach a
    number for. Exists because the coach reached past this week on its own
    anyway and, with no real figure in front of it there, invented one.

    Routed through the same `session_status` gate `_week_view` uses — not a
    second definition of "still to come". `completion.complete_planned_session`
    has no window restriction, so a runner who runs next week's long run early
    and ticks it off must not still read to the coach as something ahead of
    them: this section is INTENT only, and a completed (or dismissed, or
    somehow already missed) session is no longer intent.
    """
    start = week_start(today, starts_on) + timedelta(days=WEEK_LENGTH_DAYS)
    end = start + timedelta(days=WEEK_LENGTH_DAYS - 1)
    rows = store.sessions_in_range(db, user_id, start, end, plan_id=plan.id)

    upcoming: List[UpcomingSessionContext] = []
    for row in rows:
        if row.commitment != "committed" or row.intent == "rest":
            continue
        if session_status(row, today) != "upcoming":
            continue
        if len(upcoming) >= MAX_UPCOMING:
            break
        upcoming.append(
            UpcomingSessionContext(
                session_id=str(row.id),
                when=_when(row, today, starts_on, next_week=True),
                title=row.title,
                intent=row.intent,
                discipline=row.discipline,
                target=_target(row),
                detail=row.detail,
            )
        )
    return upcoming


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
            session_id=str(matched.id),
            title=matched.title,
            intent=matched.intent,
            discipline=matched.discipline,
            target=_target(matched),
            detail=matched.detail,
        )

    upcoming, committed, done, planned_running = _week_view(
        db,
        user_id,
        plan,
        today,
        starts_on,
        exclude_session_id=matched.id if matched is not None else None,
    )
    next_week = _next_week_committed(db, user_id, plan, today, starts_on)

    # The DERIVED statement, not the coach's own `label` (#844) — the same text
    # the runner's schedule screen shows them. A label is written by the coach
    # and tied to nothing, so it can describe a rule its own predicate does not
    # enforce (a live plan's label promised "easy cross-training" against a
    # predicate forbidding exactly that). Feeding the label here would leave the
    # runner reading one rule and the coach reasoning from another, which is
    # worse than the two being wrong together.
    rules = [describe_rule(rule) for rule in store.plan_rules(plan)][:MAX_RULES]

    if not (planned_for or upcoming or next_week or rules):
        # Nothing to say. An empty section is tokens spent on silence.
        return None

    return ScheduleContext(
        planned_for_this_activity=planned_for,
        still_to_come_this_week=upcoming,
        next_week_committed=next_week,
        rules_in_play=rules,
        committed_this_week=committed,
        done_this_week=done,
        planned_running_this_week=planned_running,
    )


def _draft_state(db: Session, user_id) -> Optional[dict]:
    """What is happening to a plan right now, when anything is (#879).

    Only the two states the coach could otherwise get wrong. A draft that landed
    needs no note: the plan itself is the evidence, and the runner asking about
    a plan that is simply there is not the case this exists for.

    A failure is reported for as long as it is the last thing that happened to
    this runner's plan — not for a fixed window afterwards. A runner whose draft
    failed and who has not since asked for another is still a runner without the
    plan they asked for, however long ago they asked, and that is exactly the
    state the coach was reading as success.
    """
    if store.draft_in_flight(db, user_id) is not None:
        return {"state": "writing"}
    failed = store.latest_failed_plan(db, user_id)
    if failed is None:
        return None
    active = store.get_active_plan(db, user_id)
    # A failure can only follow the plan it tried to replace, so it takes a tie.
    if active is None or failed.created_at >= active.created_at:
        return {"state": "failed"}
    return None


def _race_section(db: Session, user_id: Any, plan: Any, today: date) -> dict:
    """The race this plan is aimed at, and whether it is aimed at it (#973).

    Read from the runner's own `goal_races` rather than from `plan.goal_race_id`,
    because the question the coach is answering is "what is this runner training
    for", and that is a fact about the runner. The plan's pointer answers a
    different question, "was this block built for it", which is worth knowing
    separately and is exactly what a plan restored from before the race was
    stated gets wrong (#939).
    """
    races = store.list_goal_races(db, user_id, on_or_after=today)
    if not races:
        return {}
    race = next((r for r in races if r.priority == "A"), races[0])
    weeks_away = max(0, (race.race_date - today).days) / 7
    section = {
        "race": {
            "name": race.name,
            "date": race.race_date.isoformat(),
            "distance_km": round(race.distance_m / 1000, 1),
            "weeks_away": round(weeks_away, 1),
        }
    }
    if plan is not None and plan.goal_race_id != race.id:
        # Stated as a fact about the plan, not a warning to relay. A coach that
        # knows this can say the block is not phased for the date and offer to
        # fix it; a coach that does not know it describes a taper the plan never
        # had.
        section["plan_built_for_this_race"] = False
    return section


def _written_through(
    db: Session, user_id: Any, plan: Any, today: date, starts_on: int
) -> dict:
    """The last date this plan actually tells the runner what to do (#981).

    A plan whose written sessions have run out is still `active` and still has a
    horizon, so nothing the coach previously received distinguished it from one
    that covers the next two months. The runner reaches an empty week and the
    coach reads the horizon date and talks about a Peak block that holds nothing.

    Also says whether shape remains beyond that point, because the two cases lead
    the coach somewhere different: shape left is a block to write out with the
    progression already agreed, and nothing left is a plan that has genuinely run
    its course.
    """
    if plan is None:
        return {}
    last = (
        db.query(PlannedSession.window_end)
        .filter(
            PlannedSession.user_id == user_id,
            PlannedSession.plan_id == plan.id,
            PlannedSession.commitment == "committed",
        )
        .order_by(PlannedSession.window_end.desc())
        .first()
    )
    if last is None or last[0] is None:
        return {"sessions_written_through": None, "weeks_still_only_shape": 0}
    written_through = last[0]
    remaining_shapes = [
        shape
        for shape in store.plan_week_shapes(plan)
        if shape.week_start > week_start(written_through, starts_on)
    ]
    out: dict = {
        "sessions_written_through": written_through.isoformat(),
        "weeks_still_only_shape": len(remaining_shapes),
    }
    if written_through < today:
        out["plan_has_run_out"] = True
    return out


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

    Returns None when the runner has no active plan and nothing is being written
    — which is itself worth knowing, so the caller says so in words rather than
    passing an empty shell.

    A plan the runner confirmed is written on the worker over about a minute and
    can fail outright, and until #879 nothing here could tell (#879). The coach
    went on saying the plan was in while the row sat `failed`, because it was
    reading a plan that had not changed and had no way to know one had been
    attempted. `draft` carries that: what is happening to a plan right now, as
    distinct from what the plan says.
    """
    draft = _draft_state(db, user.id)
    plan = store.get_active_plan(db, user.id)
    if plan is None:
        return {"has_plan": False, "draft": draft} if draft else None

    starts_on = resolve_week_start(getattr(user, "profile", None))
    today = today or date.today()
    upcoming, committed, done, planned_running = _week_view(
        db, user.id, plan, today, starts_on
    )

    return {
        "has_plan": True,
        **({"draft": draft} if draft else {}),
        # The race, and whether this plan is actually built for it (#973/#939).
        # "Talk me through my schedule up to my half marathon" cannot be answered
        # by a coach that does not know which race is meant or how far away it
        # is, and the anchoring is a real distinction the runner cannot see: a
        # plan restored from before they stated their race carries no pointer to
        # it, so its phases were never built backwards from that date. Saying so
        # is what stops the coach describing a race build the plan does not
        # contain.
        **_race_section(db, user.id, plan, today),
        # Where the WRITTEN sessions stop (#981). A plan holds real sessions for
        # its near weeks and shape beyond, so "the plan runs to 11 Oct" and "the
        # plan tells you what to do until 30 Aug" are different facts and the
        # coach was only ever given the first. It read the horizon date as
        # coverage and talked about weeks that hold nothing.
        **_written_through(db, user.id, plan, today, starts_on),
        # So "Is it added?" can be answered from the record (#883). Without it the
        # coach could see a plan but not that it was the one written ninety
        # seconds ago, so it offered to write another and the runner confirmed.
        "written": describe_written_ago(plan),
        "runs_through": plan.horizon_end.isoformat() if plan.horizon_end else None,
        # The headline number on the runner's own screen (#880). Without it, a
        # runner asking why their week read 25 was asking the coach about a
        # figure it had never been shown, and it answered anyway: first with an
        # invented mechanism, then by diagnosing the app.
        "planned_running_this_week": planned_running,
        "still_to_come_this_week": [item.model_dump() for item in upcoming],
        "committed_this_week": committed,
        "done_this_week": done,
        # The DERIVED statement, never the coach's own `label` (#844/#847). The
        # label is LLM-written prose that nothing ties to the predicate, so it can
        # promise what the rule does not enforce — a live draft invited a walk the
        # predicate then flagged. A coach planning around the label would plan the
        # same violation into the next block, which is the original defect one
        # surface further in.
        "rules_in_play": [
            describe_rule(rule) for rule in store.plan_rules(plan)
        ][:MAX_RULES],
    }
