"""#830: the horizon — the shape of the block, not the next seven days.

Concrete for about three weeks, shape only beyond. Which is which is DERIVED
from what is actually stored, never flagged, so the horizon cannot claim a week
was planned after its sessions were removed. This file pins that derivation, the
continuous run of weeks (a gap reads as a gap, not as a shorter block), the
share-based mixes, the peak the bars are scaled against, and the race window.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

from app.models import User, UserProfile
from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule.horizon import build_horizon

TODAY = date(2026, 8, 12)  # a Wednesday
WEEK_0 = date(2026, 8, 10)  # the Monday it falls in
WEEK_1 = WEEK_0 + timedelta(days=7)
WEEK_2 = WEEK_0 + timedelta(days=14)
WEEK_3 = WEEK_0 + timedelta(days=21)


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


def _seed_plan(db, user: User, *, week_shapes=None) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id, status="active", rules=[], week_shapes=week_shapes or []
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
        target_effort_score=target_effort_score,
    )
    db.add(session)
    db.commit()
    return session


def _seed_race(db, user: User, *, race_date: date, name: str = "Race") -> GoalRace:
    race = GoalRace(
        user_id=user.id,
        name=name,
        race_date=race_date,
        distance_m=21097.5,
        priority="A",
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def _week(horizon, week_start: date):
    return next(w for w in horizon.weeks if w.week_start == week_start)


# --- planned, sketched, empty ---------------------------------------------


def test_a_week_with_sessions_is_planned_and_its_numbers_come_from_them(db):
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        week_shapes=[
            {
                "week_start": WEEK_0.isoformat(),
                "phase": "base",
                "target_running_distance_m": 99000,
                "target_effort_score": 999,
            }
        ],
    )
    _seed_session(
        db,
        plan,
        start=WEEK_0,
        discipline="run",
        intent="easy",
        target_distance_m=8000,
        target_effort_score=60.0,
    )
    _seed_session(
        db,
        plan,
        start=WEEK_0 + timedelta(days=3),
        discipline="bike",
        intent="easy",
        target_effort_score=40.0,
    )

    horizon = build_horizon(db, user, weeks=4, today=TODAY)
    week = _week(horizon, WEEK_0)

    assert week.planned is True
    assert week.is_current is True
    # The sessions, not the shape's placeholder numbers.
    assert week.running_distance_m == 8000
    assert week.effort_score == 100.0
    # The phase is still the shape's: it is the only place a phase is named.
    assert week.phase == "base"


def test_a_suggestion_carries_no_weight_in_the_blocks_shape(db):
    """A suggestion nobody has agreed to must not inflate the ramp."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=WEEK_0, target_distance_m=8000, target_effort_score=60.0
    )
    _seed_session(
        db,
        plan,
        start=WEEK_0 + timedelta(days=2),
        commitment="suggested",
        target_distance_m=25000,
        target_effort_score=300.0,
    )

    week = _week(build_horizon(db, user, weeks=2, today=TODAY), WEEK_0)

    assert (week.running_distance_m, week.effort_score) == (8000, 60.0)


def test_a_week_holding_only_suggestions_is_not_a_planned_week(db):
    """"Planned" means the runner committed to something. A week carrying only
    suggestions has no plan in it, so it falls through to its shape rather than
    drawing a solid, zero-length bar. (Reading of the design's COMMITMENT axis:
    `suggested` is explicitly "ignoring it leaves no trace".)"""
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        week_shapes=[
            {"week_start": WEEK_0.isoformat(), "phase": "base", "target_effort_score": 400}
        ],
    )
    _seed_session(
        db, plan, start=WEEK_0, commitment="suggested", target_effort_score=200.0
    )

    week = _week(build_horizon(db, user, weeks=1, today=TODAY), WEEK_0)

    assert week.planned is False
    assert week.effort_score == 400


def test_a_week_with_only_a_shape_is_not_planned_and_reads_from_the_shape(db):
    user = _seed_user(db)
    _seed_plan(
        db,
        user,
        week_shapes=[
            {
                "week_start": WEEK_2.isoformat(),
                "phase": "build",
                "target_running_distance_m": 52000,
                "target_effort_score": 420,
                "discipline_mix": {"run": 0.8, "bike": 0.2},
                "intent_mix": {"easy": 0.7, "quality": 0.3},
            }
        ],
    )

    week = _week(build_horizon(db, user, weeks=4, today=TODAY), WEEK_2)

    assert week.planned is False
    assert week.phase == "build"
    assert week.running_distance_m == 52000
    assert week.effort_score == 420
    assert week.discipline_mix == {"run": 0.8, "bike": 0.2}
    assert week.intent_mix == {"easy": 0.7, "quality": 0.3}


def test_a_week_the_plan_says_nothing_about_is_still_emitted(db):
    """A gap has to read as a gap, not as a shorter block."""
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        week_shapes=[
            {"week_start": WEEK_3.isoformat(), "phase": "peak", "target_effort_score": 500}
        ],
    )
    _seed_session(db, plan, start=WEEK_0, target_effort_score=100.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert [w.week_start for w in horizon.weeks] == [WEEK_0, WEEK_1, WEEK_2, WEEK_3]
    empty = _week(horizon, WEEK_1)
    assert empty.planned is False
    assert empty.phase is None
    assert empty.running_distance_m is None
    assert empty.effort_score is None
    assert empty.discipline_mix == {}


def test_the_horizon_is_a_continuous_run_of_weeks_from_the_current_one(db):
    user = _seed_user(db)

    horizon = build_horizon(db, user, weeks=6, today=TODAY)

    assert len(horizon.weeks) == 6
    assert horizon.has_plan is False
    assert [w.week_start for w in horizon.weeks] == [
        WEEK_0 + timedelta(days=7 * index) for index in range(6)
    ]
    assert [w.is_current for w in horizon.weeks] == [True] + [False] * 5


# --- mixes are shares ------------------------------------------------------


def test_the_mixes_are_shares_of_the_weeks_load(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db,
        plan,
        start=WEEK_0,
        discipline="run",
        intent="easy",
        target_effort_score=60.0,
    )
    _seed_session(
        db,
        plan,
        start=WEEK_0 + timedelta(days=2),
        discipline="run",
        intent="quality",
        target_effort_score=20.0,
    )
    _seed_session(
        db,
        plan,
        start=WEEK_0 + timedelta(days=4),
        discipline="strength",
        intent="strength",
        target_effort_score=20.0,
    )

    week = _week(build_horizon(db, user, weeks=2, today=TODAY), WEEK_0)

    assert week.discipline_mix == {"run": 0.8, "strength": 0.2}
    assert week.intent_mix == {"easy": 0.6, "quality": 0.2, "strength": 0.2}
    assert abs(sum(week.discipline_mix.values()) - 1.0) < 1e-6
    assert abs(sum(week.intent_mix.values()) - 1.0) < 1e-6


def test_an_all_zero_week_yields_an_empty_mix_not_a_fake_even_split(db):
    """No divide-by-zero, and no invented shares for a week carrying no load."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(db, plan, start=WEEK_0, discipline="run", intent="easy")
    _seed_session(
        db, plan, start=WEEK_0 + timedelta(days=2), discipline="bike", intent="easy"
    )

    week = _week(build_horizon(db, user, weeks=2, today=TODAY), WEEK_0)

    assert week.planned is True
    assert week.effort_score == 0.0
    assert week.discipline_mix == {}
    assert week.intent_mix == {}


# --- the peak the bars are scaled against ---------------------------------


def test_peak_effort_score_is_the_largest_weekly_load_in_the_window(db):
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        week_shapes=[
            {"week_start": WEEK_2.isoformat(), "target_effort_score": 300},
            # Outside the four-week window asked for; must not raise the peak.
            {
                "week_start": (WEEK_0 + timedelta(days=70)).isoformat(),
                "target_effort_score": 900,
            },
        ],
    )
    _seed_session(db, plan, start=WEEK_0, target_effort_score=120.0)
    _seed_session(db, plan, start=WEEK_1, target_effort_score=180.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert horizon.peak_effort_score == 300


def test_peak_effort_score_abstains_when_no_week_carries_any_load(db):
    user = _seed_user(db)
    _seed_plan(db, user)

    assert build_horizon(db, user, weeks=4, today=TODAY).peak_effort_score is None


# --- races -----------------------------------------------------------------


def test_only_races_inside_the_horizons_span_are_carried(db):
    user = _seed_user(db)
    inside = _seed_race(db, user, race_date=WEEK_2 + timedelta(days=2), name="in span")
    _seed_race(db, user, race_date=WEEK_0 + timedelta(days=60), name="beyond span")
    _seed_race(db, user, race_date=WEEK_0 - timedelta(days=7), name="already run")

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert [race.name for race in horizon.races] == ["in span"]
    assert horizon.races[0].id == inside.id


def test_another_runners_sessions_shapes_and_races_are_invisible(db):
    mine = _seed_user(db)
    theirs = _seed_user(db)
    their_plan = _seed_plan(
        db,
        theirs,
        week_shapes=[{"week_start": WEEK_1.isoformat(), "target_effort_score": 700}],
    )
    _seed_session(db, their_plan, start=WEEK_0, target_effort_score=500.0)
    _seed_race(db, theirs, race_date=WEEK_1, name="their race")

    horizon = build_horizon(db, mine, weeks=4, today=TODAY)

    assert horizon.has_plan is False
    assert horizon.races == []
    assert horizon.peak_effort_score is None
    assert all(w.planned is False for w in horizon.weeks)


def test_a_superseded_plans_sessions_do_not_shape_the_horizon(db):
    """The horizon reads the ACTIVE plan only.

    The session read was unscoped, so weeks from a replaced plan drew bars in a
    horizon whose `has_plan` came from the current one — one chart built out of
    two different plans.
    """
    user = _seed_user(db)
    old = TrainingPlan(
        user_id=user.id, status="superseded", rules=[], week_shapes=[]
    )
    db.add(old)
    db.commit()
    db.refresh(old)
    _seed_session(db, old, start=WEEK_0, target_effort_score=200.0)

    horizon = build_horizon(db, user, weeks=2, today=TODAY)

    assert horizon.has_plan is False
    assert horizon.peak_effort_score is None
    assert _week(horizon, WEEK_0).planned is False
