"""#981/#973/#939: the coach's baseline knows how far the plan actually reaches.

A plan holds real sessions for its near weeks and shape beyond, and it stays
`active` either way. Nothing the coach received distinguished a plan covering the
next two months from one that ran out on Saturday: it read `runs_through` — the
plan's HORIZON — as coverage, and talked a runner through a Peak block that held
no sessions at all.

Three facts are added here and each is pinned separately.

**Where the writing stops** is not where the plan stops. `sessions_written_through`
is the last committed session's window end; `runs_through` is the horizon. The
live bug is exactly the case where those two differ, so the fixture builds that
case rather than a convenient one.

**Whether the plan is built for the race** is not the same question as which race
the runner has. Production carries the case (#939): the owner's active plan has a
null `goal_race_id` against a stated A race, so its phases were never built
backwards from that date, and a coach that cannot see this describes a taper the
plan does not contain.

**The coach's own note on a session** now travels with the session, because
"explain my next interval session" is a question about purpose and "5 x 800 m off
120 s" answers a different one.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule.coach_view import (
    build_schedule_context,
    build_thread_schedule,
)

TODAY = date(2026, 8, 12)  # a Wednesday
WEEK_0 = date(2026, 8, 10)  # the Monday it falls in
WEEK_1 = WEEK_0 + timedelta(days=7)
WEEK_2 = WEEK_0 + timedelta(days=14)
WEEK_3 = WEEK_0 + timedelta(days=21)
WEEK_4 = WEEK_0 + timedelta(days=28)
HORIZON_END = WEEK_4 + timedelta(days=6)


def _user(db) -> User:
    user = User(email=f"reach-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=5,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _plan(db, user: User, *, week_shapes=None, horizon_end=HORIZON_END,
          goal_race_id=None) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=week_shapes or [],
        horizon_end=horizon_end,
        goal_race_id=goal_race_id,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _session(db, plan, *, start, intent="easy", **kw) -> PlannedSession:
    payload = {
        "plan_id": plan.id,
        "user_id": plan.user_id,
        "window_start": start,
        "window_end": kw.pop("end", start),
        "intent": intent,
        "discipline": kw.pop("discipline", "run"),
        "commitment": kw.pop("commitment", "committed"),
        "title": kw.pop("title", f"{intent} session"),
        "target_effort_score": kw.pop("target_effort_score", 40.0),
    }
    payload.update(kw)
    row = PlannedSession(**payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _race(db, user: User, *, when: date, priority: str = "A",
          name: str = "Autumn Half") -> GoalRace:
    race = GoalRace(
        user_id=user.id,
        name=name,
        race_date=when,
        distance_m=21097.5,
        priority=priority,
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def _shape(week_start: date, **kw) -> dict:
    shape = {"week_start": week_start.isoformat(), "phase": "build"}
    shape.update(kw)
    return shape


def _running_out_plan(db, user: User) -> TrainingPlan:
    """The exact production shape: written to the end of next week, sketched to
    the end of the month, `horizon_end` weeks past both."""
    plan = _plan(
        db,
        user,
        week_shapes=[_shape(WEEK_2), _shape(WEEK_3), _shape(WEEK_4)],
    )
    _session(db, plan, start=TODAY + timedelta(days=1), intent="quality",
             title="Thursday tempo", target_distance_m=9000)
    _session(db, plan, start=WEEK_1 + timedelta(days=5), intent="long",
             end=WEEK_1 + timedelta(days=6), title="Long run",
             target_distance_m=16500)
    return plan


# --- how far the writing reaches ---------------------------------------------


def test_where_the_sessions_stop_is_reported_apart_from_where_the_plan_stops(db):
    """The live bug, as a fixture. `runs_through` is 6 Sep and the last session
    the runner has is 23 Aug; a coach given only the first talks about three
    weeks that hold nothing."""
    user = _user(db)
    _running_out_plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["runs_through"] == HORIZON_END.isoformat()
    assert schedule["sessions_written_through"] == (
        WEEK_1 + timedelta(days=6)
    ).isoformat()
    assert schedule["sessions_written_through"] != schedule["runs_through"]


def test_the_weeks_that_are_still_only_shape_are_counted(db):
    """The two cases lead a coach somewhere different: shape left is a block to
    write out from a progression already agreed, and nothing left is a plan that
    has run its course."""
    user = _user(db)
    _running_out_plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["weeks_still_only_shape"] == 3


def test_a_shape_for_a_week_already_written_out_is_not_still_only_shape(db):
    """A week holding real sessions is planned, whatever leftover shape sits
    beside it. Counting it would tell the coach there is a block left to write
    when there is not."""
    user = _user(db)
    plan = _plan(
        db,
        user,
        week_shapes=[_shape(WEEK_0), _shape(WEEK_1), _shape(WEEK_2)],
    )
    _session(db, plan, start=WEEK_1 + timedelta(days=5), intent="long",
             title="Long run")

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["weeks_still_only_shape"] == 1


def test_a_plan_whose_written_sessions_have_all_passed_says_it_has_run_out(db):
    """The state the runner was actually in: an active plan, a horizon a month
    away, and nothing left to do."""
    user = _user(db)
    plan = _plan(db, user)
    _session(db, plan, start=TODAY - timedelta(days=4), intent="long",
             title="Last long run")

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule.get("plan_has_run_out") is True
    assert schedule["sessions_written_through"] == (
        TODAY - timedelta(days=4)
    ).isoformat()


def test_a_plan_still_holding_sessions_ahead_never_claims_to_have_run_out(db):
    """The flag is stated only when it is true. Present-and-false is a fact a
    model weighs; absent is silence, and silence is right for the ordinary case."""
    user = _user(db)
    _running_out_plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert "plan_has_run_out" not in schedule


def test_a_session_today_is_not_a_plan_that_has_run_out(db):
    """The boundary. A session whose window closes today is still something the
    runner can do today, so the plan has not run out."""
    user = _user(db)
    plan = _plan(db, user)
    _session(db, plan, start=TODAY, intent="easy", title="Today's run")

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert "plan_has_run_out" not in schedule
    assert schedule["sessions_written_through"] == TODAY.isoformat()


def test_a_plan_that_never_had_a_written_session_says_so_without_guessing(db):
    """None, not the horizon date. A plan of pure shape tells the runner nothing
    to do, and reporting its horizon here would be the original defect written
    into the new field."""
    user = _user(db)
    _plan(db, user, week_shapes=[_shape(WEEK_2)])

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["sessions_written_through"] is None
    assert schedule["weeks_still_only_shape"] == 0


def test_a_suggestion_is_not_how_far_the_plan_is_written(db):
    """A suggestion the runner may decline with no trace does not extend the
    plan's reach, the same reading the horizon already applies to a week made
    only of suggestions."""
    user = _user(db)
    plan = _plan(db, user)
    _session(db, plan, start=TODAY + timedelta(days=1), intent="easy",
             title="Committed run")
    _session(db, plan, start=WEEK_3, intent="easy", commitment="suggested",
             title="Optional extra")

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["sessions_written_through"] == (
        TODAY + timedelta(days=1)
    ).isoformat()


# --- the race, and whether the plan was built for it -------------------------


def test_the_race_is_reported_without_a_verdict_on_whether_the_plan_suits_it(db):
    """The coach is told WHAT the runner is training for, and deliberately not
    whether the block is built for it.

    `goal_race_id` looks like the answer to the second question and is only a
    proxy. The live production plan this was built against carries a null
    pointer and nonetheless ends with the race, a taper and a race-week
    sharpener, because the sessions were written for the race whatever the
    column says. Reporting the proxy handed the coach "this plan is not built
    for your race" beside a plan whose last session IS the race, and invited it
    to offer to fix something that was not broken.

    Reading the block rather than the column is the real answer and belongs to
    #939. Until then this says nothing rather than something false.
    """
    user = _user(db)
    race = _race(db, user, when=WEEK_4 + timedelta(days=5))
    _running_out_plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["race"] == {
        "name": "Autumn Half",
        "date": race.race_date.isoformat(),
        "distance_km": 21.1,
        "weeks_away": 4.4,
    }
    assert "plan_built_for_this_race" not in schedule



def test_the_race_the_coach_is_told_about_is_the_runners_own_a_race(db):
    """`A` is the runner's own ranking of what the block is for. A parkrun three
    weeks out is not the race a marathon build is aimed at, however much nearer
    it is."""
    user = _user(db)
    _race(db, user, when=TODAY + timedelta(days=10), priority="B",
          name="Club 10k")
    _race(db, user, when=WEEK_4 + timedelta(days=5), priority="A",
          name="Autumn Half")
    _plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["race"]["name"] == "Autumn Half"


def test_a_runner_with_no_race_gets_no_race_section(db):
    """An empty race object would be something for the coach to reason about
    where there is nothing to say."""
    user = _user(db)
    _plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert "race" not in schedule
    assert "plan_built_for_this_race" not in schedule


def test_a_race_that_has_already_happened_is_not_the_one_the_plan_is_aimed_at(db):
    """A past race is history. Reading it as the target would have the coach
    tapering for a date that has gone."""
    user = _user(db)
    _race(db, user, when=TODAY - timedelta(days=3), name="Last month's half")
    _plan(db, user)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert "race" not in schedule


# --- the coach's own note on a session ---------------------------------------


def test_the_note_the_coach_wrote_on_a_session_comes_back_to_it(db):
    """"Explain my next interval session" is a question about PURPOSE. Without
    this the coach could only read back its own prescription, which says what to
    do and nothing about why."""
    user = _user(db)
    plan = _plan(db, user)
    _session(
        db,
        plan,
        start=TODAY + timedelta(days=1),
        intent="quality",
        title="5 x 800m",
        detail="Threshold work: hold 10k effort, jog 2 min between.",
        target_distance_m=9000,
    )

    schedule = build_thread_schedule(db, user, today=TODAY)

    upcoming = schedule["still_to_come_this_week"]
    assert [item["detail"] for item in upcoming] == [
        "Threshold work: hold 10k effort, jog 2 min between."
    ]


def test_the_note_travels_with_next_weeks_committed_sessions_too(db):
    """The one-week-further look (#943) is where a runner asks "what is that
    for" about a session they have not reached yet, so the note has to be there
    as well as in this week's list."""
    user = _user(db)
    plan = _plan(db, user)
    _session(
        db,
        plan,
        start=WEEK_1 + timedelta(days=2),
        intent="quality",
        title="Cruise intervals",
        detail="4 x 2 km at threshold, 90 s float.",
        target_distance_m=12000,
    )
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0),
        type="Ride",
        name="Commute",
        distance_m=12000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)

    context = build_schedule_context(db, activity)

    assert [item.detail for item in context.next_week_committed] == [
        "4 x 2 km at threshold, 90 s float."
    ]


def test_a_session_with_no_note_carries_none_rather_than_an_empty_string(db):
    """The coach writes a note when it has something to say. An empty string
    reads as a note that says nothing, which is a different claim."""
    user = _user(db)
    plan = _plan(db, user)
    _session(db, plan, start=TODAY + timedelta(days=1), intent="easy",
             title="Easy 8k", target_distance_m=8000)

    schedule = build_thread_schedule(db, user, today=TODAY)

    assert schedule["still_to_come_this_week"][0]["detail"] is None
