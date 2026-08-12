"""#830: the week read — one builder, planned and free.

Free mode is not a second screen: it is this same read with no active plan, the
runner's own norm in place of a target. This file pins that, the "a suggestion
you ignore leaves no trace" rule, the planned-vs-logged discipline mix, tenant
scoping, and the one definition of a run the running-km headline inherits from
`activity_facts.is_run`.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule.week import build_week

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
FRI = MON + timedelta(days=4)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)


def _seed_user(db) -> User:
    user = User(email=f"sched-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_plan(db, user: User, *, rules=None, status: str = "active") -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id, status=status, rules=rules or [], week_shapes=[]
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_session(
    db,
    plan: TrainingPlan,
    *,
    start: date,
    end: date = None,
    intent: str = "easy",
    discipline: str = "run",
    commitment: str = "committed",
    title: str = "Session",
    target_distance_m: float = None,
    target_effort_score: float = None,
    structure: dict = None,
    completed_at: datetime = None,
    dismissed_at: datetime = None,
) -> PlannedSession:
    session = PlannedSession(
        plan_id=plan.id,
        user_id=plan.user_id,
        window_start=start,
        window_end=end or start,
        intent=intent,
        discipline=discipline,
        commitment=commitment,
        title=title,
        target_distance_m=target_distance_m,
        structure=structure,
        target_effort_score=target_effort_score,
        completed_at=completed_at,
        dismissed_at=dismissed_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _seed_activity(
    db,
    user: User,
    *,
    day: date,
    activity_type: str = "Run",
    distance_m: int = 8000,
    effort_score: float = 30.0,
) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0),
        type=activity_type,
        name=activity_type,
        distance_m=distance_m,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(
        DerivedMetric(
            activity_id=activity.id, effort_score=effort_score, confidence="high"
        )
    )
    db.commit()
    db.refresh(activity)
    return activity


def _discipline(week, name):
    return next((row for row in week.by_discipline if row.discipline == name), None)


# --- free mode -------------------------------------------------------------


def test_free_mode_reports_no_plan_but_still_reports_what_was_run(db):
    user = _seed_user(db)
    _seed_activity(db, user, day=TUE, distance_m=8000)
    _seed_activity(db, user, day=WED, activity_type="Walk", distance_m=3000)

    week = build_week(db, user, today=THU)

    assert week.has_plan is False
    assert week.plan_id is None
    assert week.headline.planned_running_distance_m is None
    assert week.headline.logged_running_distance_m == 8000
    assert len(week.logged) == 2
    assert {row.discipline for row in week.by_discipline} == {"run", "walk"}


def test_the_week_boundary_and_flags_come_from_the_runners_own_week(db):
    user = _seed_user(db)

    week = build_week(db, user, today=THU)

    assert (week.week_start, week.week_end) == (MON, SUN)
    assert week.is_current_week is True


# --- the norm --------------------------------------------------------------


def test_the_norm_is_carried_for_the_current_week_only(db):
    """"As of today" is only meaningful for the week today is in."""
    user = _seed_user(db)
    for offset in range(1, 60):
        _seed_activity(db, user, day=THU - timedelta(days=offset))

    current = build_week(db, user, today=THU)
    past = build_week(db, user, target_week=MON - timedelta(days=7), today=THU)
    future = build_week(db, user, target_week=MON + timedelta(days=7), today=THU)

    assert current.norm is not None
    assert {row.metric for row in current.norm} == {
        "sessions",
        "distance_m",
        "moving_time_s",
        "effort_score",
    }
    assert past.norm is None
    assert past.is_current_week is False
    assert future.norm is None
    assert future.is_current_week is False


# --- a suggestion you ignore leaves no trace -------------------------------


def test_a_dismissed_or_lapsed_suggestion_leaves_no_trace_but_a_missed_commitment_stays(db):
    """Otherwise free mode becomes a plan with extra guilt. Missing something you
    AGREED to is exactly what the coach should still see."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        start=TUE,
        commitment="suggested",
        title="dismissed suggestion",
        dismissed_at=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )
    _seed_session(
        db, plan, start=WED, commitment="suggested", title="lapsed suggestion"
    )
    _seed_session(
        db, plan, start=TUE, commitment="committed", title="missed commitment"
    )
    _seed_session(
        db, plan, start=SAT, commitment="suggested", title="live suggestion"
    )

    week = build_week(db, user, today=FRI)

    by_title = {s.title: s for s in week.sessions}
    assert set(by_title) == {"missed commitment", "live suggestion"}
    assert by_title["missed commitment"].status == "missed"
    assert by_title["live suggestion"].status == "upcoming"


# --- planned vs logged -----------------------------------------------------


def test_by_discipline_sums_planned_and_logged_separately_and_lists_only_what_appears(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=TUE, discipline="run", target_effort_score=40.0
    )
    _seed_session(
        db,
        plan,
        start=THU,
        discipline="strength",
        intent="strength",
        target_effort_score=20.0,
    )
    _seed_activity(db, user, day=TUE, activity_type="Run", effort_score=35.0)
    _seed_activity(db, user, day=WED, activity_type="Ride", effort_score=25.0)

    week = build_week(db, user, today=FRI)

    run = _discipline(week, "run")
    assert (run.planned_effort_score, run.planned_sessions) == (40.0, 1)
    assert (run.logged_effort_score, run.logged_sessions) == (35.0, 1)

    strength = _discipline(week, "strength")
    assert (strength.planned_effort_score, strength.planned_sessions) == (20.0, 1)
    assert (strength.logged_effort_score, strength.logged_sessions) == (0.0, 0)

    bike = _discipline(week, "bike")
    assert (bike.planned_effort_score, bike.planned_sessions) == (0.0, 0)
    assert (bike.logged_effort_score, bike.logged_sessions) == (25.0, 1)

    # Only what is actually there, in the fixed mix-bar order.
    assert [row.discipline for row in week.by_discipline] == ["run", "bike", "strength"]


def test_the_planned_running_headline_counts_committed_runs_only(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, discipline="run", target_distance_m=10000)
    _seed_session(
        db,
        plan,
        start=THU,
        discipline="run",
        commitment="suggested",
        target_distance_m=6000,
    )
    _seed_session(db, plan, start=WED, discipline="bike", target_distance_m=30000)

    week = build_week(db, user, today=MON)

    assert week.has_plan is True
    assert week.plan_id == plan.id
    assert week.headline.planned_running_distance_m == 10000


def test_an_interval_session_counts_warmup_reps_and_cooldown(db):
    """The session, not just its work -- the whole of #876.

    Rep structure is a first-class way to SIZE a run: `plan_validator` accepts it
    in place of a distance precisely because "6 x 400m off 90s" is a fully
    specified session a coach would never also write as a total. But the headline
    only ever summed `target_distance_m`, so every one of those sessions landed
    in the week as zero km -- a real week read 23.5 km with a 6 x 400m in it and
    its distance nowhere.

    Counting only the reps was still wrong: it made that session 2.4 km when the
    runner had agreed 4.5 km door to door. The warm-up and cool-down were the
    missing 2.1 km, and they were unreachable while the coach wrote them as
    MINUTES in prose -- recovering a distance from "10 min easy" means assuming a
    pace, which is the app inventing distance. So the prescription changed: they
    are stated as distances now, and this is addition rather than inference.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, discipline="run", target_distance_m=5000)
    _seed_session(
        db,
        plan,
        start=WED,
        end=FRI,
        discipline="run",
        intent="quality",
        title="Intervals: 6x400m @ 4:30/km",
        structure={
            "reps_planned": 6,
            "rep_distance_m": 400.0,
            "rest_s": 90.0,
            "warmup_distance_m": 1100.0,
            "cooldown_distance_m": 1000.0,
        },
    )

    week = build_week(db, user, today=MON)

    # 1.1 warm-up + 2.4 of reps + 1.0 cool-down = the 4.5 km session, + the easy 5.
    assert week.headline.planned_running_distance_m == 9500


def test_each_part_of_an_interval_session_stands_on_its_own(db):
    """A part the coach did not write contributes nothing, and blocks nothing.

    A warm-up with no cool-down is a real prescription, and so is a warm-up
    either side of reps that carry no distance of their own. The missing part
    must not zero the parts that were stated, nor be filled in with a guess.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        start=TUE,
        discipline="run",
        intent="quality",
        structure={"reps_planned": 6, "rep_distance_m": 400.0, "warmup_distance_m": 1100.0},
    )
    _seed_session(
        db,
        plan,
        start=THU,
        discipline="run",
        intent="quality",
        structure={"reps_planned": 8, "warmup_distance_m": 1000.0, "cooldown_distance_m": 1000.0},
    )

    week = build_week(db, user, today=MON)

    # (1.1 + 2.4) + (1.0 + 1.0, the 8 unmeasured reps adding nothing)
    assert week.headline.planned_running_distance_m == 5500


def test_a_rep_structured_run_that_also_states_a_total_is_not_double_counted(db):
    """`target_distance_m` wins when both are present.

    A coach who writes a total has stated the whole session -- warm-up and
    cool-down included -- so adding the structured parts on top would count them
    twice.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        start=WED,
        discipline="run",
        intent="quality",
        target_distance_m=9000,
        structure={
            "reps_planned": 6,
            "rep_distance_m": 400.0,
            "warmup_distance_m": 1000.0,
            "cooldown_distance_m": 1000.0,
        },
    )

    week = build_week(db, user, today=MON)

    assert week.headline.planned_running_distance_m == 9000


def test_reps_with_no_measurement_at_all_add_nothing_to_the_headline(db):
    """"6 reps" with no distance on them is not a distance, and is not guessed."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, discipline="run", target_distance_m=5000)
    _seed_session(
        db,
        plan,
        start=WED,
        discipline="run",
        intent="quality",
        structure={"reps_planned": 6, "rest_s": 90.0},
    )

    week = build_week(db, user, today=MON)

    assert week.headline.planned_running_distance_m == 5000


# --- ONE definition of a run ----------------------------------------------


def test_only_a_strict_run_counts_toward_running_km_by_deliberate_design(db):
    """The running-km headline is `activity_facts.is_run`, which is strictly
    `type == "run"`. `TrailRun` and `VirtualRun` therefore do NOT count here and
    land in `other`.

    This is INTENDED, not an oversight: the schedule's headline has to be the
    same number the Trends page shows, and a second, more generous run set living
    in the schedule would make the two disagree about the same week with nothing
    to say which was right. Widening what counts as a run is #644's job, in
    `is_run`, once — and this read inherits that fix on the day it lands. If this
    assertion starts failing because `is_run` widened, the correct change is to
    update the expected numbers here, never to give the schedule its own run set.
    """
    user = _seed_user(db)
    _seed_activity(db, user, day=TUE, activity_type="Run", distance_m=10000)
    _seed_activity(db, user, day=WED, activity_type="TrailRun", distance_m=12000)
    _seed_activity(db, user, day=THU, activity_type="VirtualRun", distance_m=5000)

    week = build_week(db, user, today=FRI)

    assert week.headline.logged_running_distance_m == 10000

    by_type = {row.activity_type: row.discipline for row in week.logged}
    assert by_type == {"Run": "run", "TrailRun": "other", "VirtualRun": "other"}

    other = _discipline(week, "other")
    assert other.logged_sessions == 2
    assert _discipline(week, "run").logged_sessions == 1


# --- rules ride the week ---------------------------------------------------


def test_the_weeks_rules_and_the_violation_they_cannot_absorb_are_both_reported(db):
    """The point of typed rules: the week can show the runner WHY a placement
    does not work, instead of the rule sitting unreadable in a chat message."""
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        rules=[
            {
                "kind": "no_intent_day_before",
                "label": "No quality run the day before the long run",
                "before_intent": "quality",
                "target_intent": "long",
            }
        ],
    )
    _seed_session(db, plan, start=SAT, intent="quality", title="Intervals")
    _seed_session(db, plan, start=SUN, intent="long", title="Long run")

    week = build_week(db, user, today=MON)

    assert [rule.kind for rule in week.rules] == ["no_intent_day_before"]
    assert [v.kind for v in week.violations] == ["no_intent_day_before"]
    assert week.violations[0].label == "No quality run the day before the long run"
    # #844: both the rule and its violation carry the derived STATEMENT
    # alongside the coach's label, and the two are not the same text here.
    assert week.rules[0].statement == "No quality session the day before a long session."
    assert week.violations[0].statement == week.rules[0].statement


def test_844_a_misleading_label_cannot_make_its_own_rule_statement_permissive(db):
    """The real incident: a `rest_day_after` rule whose stored label invites a
    walk must still present a statement that forbids one — the statement is
    derived from `kind`/`intent`, never from `label`."""
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        rules=[
            {
                "kind": "rest_day_after",
                "label": "Full rest or easy walk only the day after the long run",
                "intent": "long",
            }
        ],
    )
    _seed_session(db, plan, start=SAT, intent="long", title="Long run")
    _seed_session(db, plan, start=SUN, intent="easy", discipline="walk", title="Walk")

    week = build_week(db, user, today=MON)

    assert len(week.rules) == 1
    assert "walk" not in week.rules[0].statement.lower()
    assert week.rules[0].statement == "Nothing but rest the day after a long session."
    assert [v.kind for v in week.violations] == ["rest_day_after"]
    assert "walk" not in week.violations[0].statement.lower()
    assert week.violations[0].statement == week.rules[0].statement


def test_an_off_shape_stored_rule_is_dropped_rather_than_taking_down_the_week(db):
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        rules=[
            {"kind": "rest_day_after", "label": "missing its intent"},
            {
                "kind": "preferred_days",
                "label": "Long run needs a free morning",
                "intent": "long",
                "weekdays": [5, 6],
            },
        ],
    )
    _seed_session(db, plan, start=SAT, intent="long", title="Long run")

    week = build_week(db, user, today=MON)

    assert [rule.kind for rule in week.rules] == ["preferred_days"]
    assert week.violations == []


# --- tenant scoping --------------------------------------------------------


def test_another_runners_plan_sessions_and_activities_are_invisible(db):
    mine = _seed_user(db)
    theirs = _seed_user(db)
    their_plan = _seed_plan(db, theirs)
    _seed_session(db, their_plan, start=TUE, title="their session")
    _seed_activity(db, theirs, day=TUE, distance_m=21000)

    my_plan = _seed_plan(db, mine)
    _seed_session(db, my_plan, start=WED, title="my session")
    _seed_activity(db, mine, day=WED, distance_m=5000)

    week = build_week(db, mine, today=THU)

    assert week.plan_id == my_plan.id
    assert [s.title for s in week.sessions] == ["my session"]
    assert week.headline.logged_running_distance_m == 5000
    assert len(week.logged) == 1


# --- regressions found during review of this slice --------------------------
#
# Each of these reproduces a defect that was present in the first cut of the
# read path and is now fixed. They are here rather than in a separate file
# because the behaviour they pin is the week read's, not the bug's.


def test_a_superseded_plans_sessions_do_not_appear_in_the_week(db):
    """A replaced plan is history.

    The session read used to be unscoped, so a superseded plan's sessions were
    listed under a headline computed from the ACTIVE plan — two plans on one
    screen, with nothing saying which the runner was following.
    """
    user = _seed_user(db)
    old = _seed_plan(db, user, status="superseded")
    _seed_session(db, old, start=WED, title="From the replaced plan")

    week = build_week(db, user, target_week=MON, today=MON)

    assert week.has_plan is False
    assert week.sessions == []


def test_sessions_from_a_plan_that_is_not_active_are_never_read(db):
    """With no active plan at all, planned sessions are not shown.

    The read reported has_plan=False and planned_running_distance_m=None while
    still listing the sessions — a free week with someone else's plan on it.
    """
    user = _seed_user(db)
    orphaned = _seed_plan(db, user, status="failed")
    _seed_session(db, orphaned, start=TUE, target_distance_m=10_000)

    week = build_week(db, user, target_week=MON, today=MON)

    assert week.has_plan is False
    assert week.headline.planned_running_distance_m is None
    assert week.sessions == []


def test_a_completed_rest_day_cannot_make_done_exceed_planned(db):
    """"3 of 7 sessions" is a count of WORK, on both sides of the pair.

    `planned_sessions` excluded a prescribed rest and `done_sessions` did not,
    so ticking a rest card produced the headline "0 planned, 1 done".
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        start=MON,
        intent="rest",
        discipline="other",
        title="Rest",
        completed_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
    )

    week = build_week(db, user, target_week=MON, today=MON)

    assert week.headline.planned_sessions == 0
    assert week.headline.done_sessions == 0
    assert week.headline.done_sessions <= week.headline.planned_sessions


def test_one_off_vocabulary_session_is_dropped_not_fatal(db):
    """The plan is LLM-written, so a row can carry a discipline outside the set.

    That used to raise out of the serializer and 500 the whole screen. One bad
    card must cost one card — the containment the JSON columns already have.
    """
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=TUE, discipline="swim", title="Off-vocabulary")
    _seed_session(db, plan, start=WED, discipline="run", title="Fine")

    week = build_week(db, user, target_week=MON, today=MON)

    assert [s.title for s in week.sessions] == ["Fine"]


def test_the_read_says_when_a_window_has_narrowed(db):
    """The design shows "window narrowed from Thu-Sat"; the read carries it."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=THU, end=SAT, title="Floating")

    on_thursday = build_week(db, user, target_week=MON, today=THU)
    assert on_thursday.sessions[0].has_narrowed is False
    assert on_thursday.sessions[0].effective_window_start == THU

    on_friday = build_week(db, user, target_week=MON, today=FRI)
    assert on_friday.sessions[0].has_narrowed is True
    assert on_friday.sessions[0].effective_window_start == FRI
    # The STORED window never moves; only the effective one does.
    assert on_friday.sessions[0].window_start == THU
