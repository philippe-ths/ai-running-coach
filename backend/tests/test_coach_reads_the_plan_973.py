"""#973: the coach can read the whole block, and reads the same one the runner does.

Asked "what happens between here and my race", the coach had no tool that could
return a plan. Every tool it held reads what the runner has already DONE, so it
reached for training history and answered from that. A live conversation produced
"when you step up to the 16.5 km run on Aug 31" about a week the plan had written
no sessions for at all: a distance, a day and a confident tone, over nothing.

Two properties are what make the tool an answer rather than a second opinion.

**One answer, not two.** The tool is built on `build_horizon`, the same builder
behind the runner's own Schedule screen. The oracle here is therefore not a
hand-written expectation of what the plan says — it is the horizon itself. The
two are asserted EQUAL week by week, because the failure this guards against is
the two drifting apart, and a pair of hand-written expectations would go stale
together and never notice.

**A sketch is never mistaken for a promise.** Every week says which of the two it
is. Handing both over unlabelled reproduces the original defect one level up.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

from app.models import User, UserProfile
from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.coach import query_tools as qt
from app.services.schedule.horizon import build_horizon

TODAY = date(2026, 8, 12)  # a Wednesday
WEEK_0 = date(2026, 8, 10)  # the Monday it falls in
WEEK_1 = WEEK_0 + timedelta(days=7)
WEEK_2 = WEEK_0 + timedelta(days=14)
WEEK_3 = WEEK_0 + timedelta(days=21)
WEEK_4 = WEEK_0 + timedelta(days=28)


def _user(db) -> User:
    user = User(email=f"plan-tool-{uuid4()}@example.com")
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


def _plan(db, user: User, *, week_shapes=None, horizon_end=None) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=week_shapes or [],
        horizon_end=horizon_end,
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
    return row


def _shape(week_start: date, **kw) -> dict:
    shape = {
        "week_start": week_start.isoformat(),
        "phase": "build",
        "target_running_distance_m": 34000.0,
        "target_effort_score": 240.0,
        "long_run_distance_m": 18000.0,
        "quality_focus": "cruise intervals",
    }
    shape.update(kw)
    return shape


def _a_real_shaped_block(db, user: User) -> TrainingPlan:
    """Two written weeks, three sketched ones, and a horizon that stops there.

    The production shape the issue is about: near weeks written out, later weeks
    agreed in outline, and a plan whose reach ends before the 12-week horizon the
    tool asks for — so there are `beyond_plan` weeks to exclude.
    """
    plan = _plan(
        db,
        user,
        week_shapes=[
            _shape(WEEK_2, phase="build", long_run_distance_m=20000.0),
            _shape(WEEK_3, phase="peak", quality_focus="race-pace tempo"),
            _shape(WEEK_4, phase="taper", target_running_distance_m=22000.0,
                   long_run_distance_m=None, quality_focus=None),
        ],
        horizon_end=WEEK_4 + timedelta(days=6),
    )
    _session(db, plan, start=WEEK_0 + timedelta(days=3), intent="easy",
             target_distance_m=8000)
    _session(db, plan, start=WEEK_0 + timedelta(days=5), intent="long",
             target_distance_m=15000)
    _session(db, plan, start=WEEK_1 + timedelta(days=2), intent="quality",
             target_distance_m=9000)
    _session(db, plan, start=WEEK_1 + timedelta(days=5), intent="long",
             target_distance_m=16500)
    return plan


# --- the key oracle: the coach and the screen are one answer -----------------


def test_the_tool_and_the_runners_own_schedule_screen_are_one_answer(db):
    """The point of the issue, asserted as an EQUALITY rather than as two
    expectations.

    Whatever the horizon says a week holds, the tool says the same: the same
    weeks, the same phase, the same running distance, the same long run, the same
    written-or-sketched verdict. A hand-written expectation on each side would
    let the two drift and stay green, which is exactly the failure mode ("your
    screen says one thing, the coach said another") the tool exists to prevent.
    """
    user = _user(db)
    _a_real_shaped_block(db, user)

    horizon = build_horizon(db, user, today=TODAY)
    tool = qt.get_training_plan(db, user.id, today=TODAY)

    def _facts_from_horizon(week):
        return {
            "week_start": week.week_start.isoformat(),
            "written": week.coverage == "planned",
            "phase": week.phase,
            "running_km": (
                round(week.running_distance_m / 1000, 1)
                if week.running_distance_m
                else None
            ),
            "long_run_km": (
                round(week.long_run_distance_m / 1000, 1)
                if week.long_run_distance_m
                else None
            ),
        }

    expected = [
        _facts_from_horizon(w)
        for w in horizon.weeks
        if w.coverage != "beyond_plan"
    ]
    got = [
        {
            "week_start": entry["week_start"],
            "written": entry["written"],
            "phase": entry["phase"],
            "running_km": entry.get("running_km"),
            "long_run_km": entry.get("long_run_km"),
        }
        for entry in tool["weeks"]
    ]

    assert got == expected
    # The comparison above is only worth anything if it compares something. This
    # fixture must actually contain both kinds of week and real numbers on each,
    # or two empty lists would satisfy it.
    assert len(expected) >= 5
    assert {e["written"] for e in expected} == {True, False}
    assert [e for e in expected if e["written"] and e["long_run_km"]]
    assert [e for e in expected if not e["written"] and e["long_run_km"]]


def test_a_written_weeks_long_run_reaches_the_coach_as_the_runner_would_say_it(db):
    """The concrete half of the equality above, stated once in plain numbers so a
    reader can see what the tool actually returns — 16.5 km, the exact figure the
    live conversation invented for a week that held nothing."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    tool = qt.get_training_plan(db, user.id, today=TODAY)
    second_week = next(
        w for w in tool["weeks"] if w["week_start"] == WEEK_1.isoformat()
    )

    assert second_week["written"] is True
    assert second_week["long_run_km"] == 16.5
    assert second_week["running_km"] == 25.5


# --- a sketch is never mistaken for a promise -------------------------------


def test_every_week_the_tool_returns_says_which_of_the_two_it_is(db):
    """Not "most weeks". A week whose status is missing is a week the coach reads
    as written, because that is what a plan usually means."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    weeks = qt.get_training_plan(db, user.id, today=TODAY)["weeks"]

    assert weeks, "the fixture must return weeks for this to mean anything"
    assert all(isinstance(w.get("written"), bool) for w in weeks)


def test_a_shape_only_week_says_so_in_words_as_well_as_a_flag(db):
    """The anti-hallucination property. A boolean is a fact the model may or may
    not weigh; the note and the reading instruction say what NOT to do with it,
    which is what stops "the 16.5 km run on Aug 31" for a week nobody wrote."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    result = qt.get_training_plan(db, user.id, today=TODAY)
    sketched = next(
        w for w in result["weeks"] if w["week_start"] == WEEK_2.isoformat()
    )

    assert sketched["written"] is False
    assert "shape only" in sketched["note"]
    assert "no sessions written yet" in sketched["note"]
    assert "never name a session, a day or a distance" in result["how_to_read"]


def test_the_two_reaches_of_a_plan_are_reported_separately(db):
    """"The plan runs to 6 Sep" and "the plan tells you what to do until 23 Aug"
    are different facts (#981). The coach was previously given only the first and
    read it as coverage."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    result = qt.get_training_plan(db, user.id, today=TODAY)

    # Both keys say WEEK STARTING in their names. The baseline states the same
    # two facts as days, and a coach holding a week start and a day for what
    # sounds like one question eventually says the wrong one out loud.
    assert result["plan_covers_through_week_starting"] == WEEK_4.isoformat()
    assert result["sessions_written_through_week_starting"] == WEEK_1.isoformat()
    assert (
        result["plan_covers_through_week_starting"]
        != result["sessions_written_through_week_starting"]
    )


def test_weeks_past_the_plans_own_reach_are_left_out_entirely(db):
    """A `beyond_plan` week is one the plan never claimed. Listing it — even as
    empty — invites the coach to describe it."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    horizon = build_horizon(db, user, today=TODAY)
    beyond = [w for w in horizon.weeks if w.coverage == "beyond_plan"]
    assert beyond, "the fixture must produce beyond-plan weeks for this to bite"

    returned = {
        w["week_start"] for w in qt.get_training_plan(db, user.id, today=TODAY)["weeks"]
    }

    assert returned.isdisjoint({w.week_start.isoformat() for w in beyond})
    assert max(returned) == WEEK_4.isoformat()


def test_the_races_the_block_is_aimed_at_come_back_with_it(db):
    """"Talk me through my schedule up to my half" needs the race as well as the
    weeks, and the runner's own priority ranking with it."""
    user = _user(db)
    _a_real_shaped_block(db, user)
    db.add(
        GoalRace(
            user_id=user.id,
            name="Autumn Half",
            race_date=WEEK_4 + timedelta(days=5),
            distance_m=21097.5,
            priority="A",
        )
    )
    db.commit()

    races = qt.get_training_plan(db, user.id, today=TODAY)["races"]

    assert races == [
        {
            "name": "Autumn Half",
            "date": (WEEK_4 + timedelta(days=5)).isoformat(),
            "distance_km": 21.1,
            "priority": "A",
        }
    ]


# --- the runner with no plan ------------------------------------------------


def test_a_runner_with_no_plan_gets_a_real_answer_not_an_error(db):
    """Free mode is a destination, not a fetch failure. Read as an error the
    coach tells a runner to go and check another app, which is the behaviour
    #856 was raised for."""
    user = _user(db)

    result = qt.get_training_plan(db, user.id, today=TODAY)

    assert result.get("has_plan") is False
    assert "error" not in result
    assert result["week_count"] == 0
    assert "no active training plan" in result["note"]
    # And the note has to say what to DO, or the coach invents its own reading.
    assert "supported way to use the app" in result["note"]


def test_a_superseded_plan_is_not_the_plan_the_coach_reads(db):
    """The horizon reads the ACTIVE plan only, and the tool inherits that: a plan
    the runner stepped away from is history, and describing it would be
    describing weeks nobody is training to."""
    user = _user(db)
    plan = _a_real_shaped_block(db, user)
    plan.status = "superseded"
    db.commit()

    assert qt.get_training_plan(db, user.id, today=TODAY)["has_plan"] is False


# --- owner scoping (the security surface) -----------------------------------


def test_the_tool_never_reaches_another_runners_plan(db):
    """The tool takes NO model-supplied id — the runner is the one the turn is
    for. This proves that holds with a second runner present whose plan is
    distinguishable in every field."""
    mine, theirs = _user(db), _user(db)
    _a_real_shaped_block(db, mine)

    other_plan = _plan(
        db,
        theirs,
        week_shapes=[_shape(WEEK_2, phase="THEIR PHASE",
                            long_run_distance_m=42000.0)],
        horizon_end=WEEK_2 + timedelta(days=6),
    )
    _session(db, other_plan, start=WEEK_0 + timedelta(days=1), intent="long",
             target_distance_m=99000)

    result = qt.get_training_plan(db, mine.id, today=TODAY)

    long_runs = [w.get("long_run_km") for w in result["weeks"]]
    assert 99.0 not in long_runs
    assert 42.0 not in long_runs
    assert "THEIR PHASE" not in [w["phase"] for w in result["weeks"]]
    # And the other runner still reads their own.
    theirs_result = qt.get_training_plan(db, theirs.id, today=TODAY)
    assert 99.0 in [w.get("long_run_km") for w in theirs_result["weeks"]]


def test_an_unknown_user_id_is_not_found_rather_than_someone_elses_plan(db):
    _user(db)  # a runner with a plan exists, and must not be the fallback
    result = qt.get_training_plan(db, uuid4(), today=TODAY)
    assert result == {"error": "not_found"}


# --- the dispatch seam ------------------------------------------------------


def test_the_tool_is_reachable_through_the_dispatcher_the_chat_turn_uses(db):
    """A tool the model can name and the dispatcher cannot run is a tool that
    returns `unknown_tool` in production while its own unit tests pass."""
    user = _user(db)
    _a_real_shaped_block(db, user)

    declared = {tool["name"] for tool in qt.CHAT_TOOLS}
    assert "get_training_plan" in declared

    direct = qt.get_training_plan(db, user.id, today=TODAY)
    dispatched = qt.execute_chat_tool(
        db, user.id, "get_training_plan", {}, today=TODAY
    )

    assert dispatched == direct
    assert dispatched["has_plan"] is True


def test_the_trace_says_how_far_ahead_the_coach_actually_read(db):
    """What the runner can check afterwards: the block the coach read, and how
    many of those weeks it was told hold real sessions."""
    user = _user(db)
    _a_real_shaped_block(db, user)
    result = qt.get_training_plan(db, user.id, today=TODAY)

    entry = qt.summarize_tool_call("get_training_plan", {}, result)

    assert entry["label"] == "Read your training plan"
    # The number carries its own noun. The client renders a bare `count` as
    # "sessions", so putting a WEEK count there rendered "7 sessions" for a
    # seven-week block: a small false number on the one affordance that exists
    # so the runner can check what the coach actually read.
    weeks = result["week_count"]
    assert entry["detail"] == f"{weeks} weeks, to {WEEK_4.isoformat()}"
    assert entry["count"] is None
