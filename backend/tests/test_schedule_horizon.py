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
WEEK_4 = WEEK_0 + timedelta(days=28)


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


def _seed_plan(
    db, user: User, *, week_shapes=None, horizon_end: date = None
) -> TrainingPlan:
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


# --- coverage: planned / sketched / empty / beyond_plan (#842) -------------


def test_coverage_is_planned_when_the_week_holds_a_committed_session(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user, horizon_end=WEEK_3 + timedelta(days=6))
    _seed_session(db, plan, start=WEEK_0, target_effort_score=60.0)

    week = _week(build_horizon(db, user, weeks=4, today=TODAY), WEEK_0)

    assert week.coverage == "planned"
    assert week.planned is True


def test_coverage_is_sketched_when_only_a_week_shape_is_present(db):
    user = _seed_user(db)
    _seed_plan(
        db,
        user,
        horizon_end=WEEK_3 + timedelta(days=6),
        week_shapes=[
            {"week_start": WEEK_2.isoformat(), "phase": "build", "target_effort_score": 420}
        ],
    )

    week = _week(build_horizon(db, user, weeks=4, today=TODAY), WEEK_2)

    assert week.coverage == "sketched"
    assert week.planned is False


def test_coverage_is_empty_for_an_interior_gap_inside_the_plans_own_span(db):
    """The #842 distinction: a gap INSIDE the plan's span reads `empty`, not
    `beyond_plan` — the two used to be the same byte-identical `else` branch,
    which is what let a week the coach never sketched print "Shape only, not
    written yet" indistinguishably from one it genuinely left blank."""
    user = _seed_user(db)
    plan = _seed_plan(db, user, horizon_end=WEEK_3 + timedelta(days=6))
    _seed_session(db, plan, start=WEEK_0, target_effort_score=60.0)
    # WEEK_1 and WEEK_2 say nothing at all, but the plan's horizon_end reaches
    # WEEK_3, so they are still inside the plan's own span.

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert _week(horizon, WEEK_1).coverage == "empty"
    assert _week(horizon, WEEK_2).coverage == "empty"


def test_a_plan_shorter_than_the_window_reads_its_tail_as_beyond_plan(db):
    """#842's own regression: `build_horizon` always walks a fixed N weeks
    regardless of how far the plan reaches, so a plan covering only the first
    two of a four-week window used to render its trailing two weeks with the
    exact same hollow "shape only, not written yet" tick as a genuinely
    sketched week — a false claim, since the coach never sketched them."""
    user = _seed_user(db)
    plan = _seed_plan(db, user, horizon_end=WEEK_1 + timedelta(days=6))
    _seed_session(db, plan, start=WEEK_0, target_effort_score=60.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert _week(horizon, WEEK_0).coverage == "planned"
    assert _week(horizon, WEEK_1).coverage == "empty"
    assert _week(horizon, WEEK_2).coverage == "beyond_plan"
    assert _week(horizon, WEEK_3).coverage == "beyond_plan"


def test_with_no_active_plan_no_week_is_ever_beyond_plan(db):
    """With no plan there is nothing to be beyond — `has_plan=False` already
    tells the client that, so every week reads `empty` instead. The main edge
    case the design calls out."""
    user = _seed_user(db)

    horizon = build_horizon(db, user, weeks=6, today=TODAY)

    assert horizon.has_plan is False
    assert all(w.coverage == "empty" for w in horizon.weeks)
    assert not any(w.coverage == "beyond_plan" for w in horizon.weeks)


def test_horizon_end_null_falls_back_to_the_latest_shape_or_committed_session(db):
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        week_shapes=[
            {"week_start": WEEK_2.isoformat(), "phase": "build", "target_effort_score": 300}
        ],
    )
    _seed_session(db, plan, start=WEEK_1, target_effort_score=60.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    # The later of the shape (WEEK_2) and the committed session (WEEK_1) is
    # WEEK_2, so only WEEK_3 falls past it.
    assert _week(horizon, WEEK_1).coverage == "planned"
    assert _week(horizon, WEEK_2).coverage == "sketched"
    assert _week(horizon, WEEK_3).coverage == "beyond_plan"


def test_horizon_end_null_fallback_ignores_a_merely_suggested_session(db):
    """A suggestion nobody has agreed to does not extend the plan's reach — the
    same reading `planned` already applies to whether a week counts as written
    at all."""
    user = _seed_user(db)
    plan = _seed_plan(db, user)
    _seed_session(
        db, plan, start=WEEK_3, commitment="suggested", target_effort_score=60.0
    )

    horizon = build_horizon(db, user, weeks=5, today=TODAY)

    assert _week(horizon, WEEK_0).coverage == "empty"
    assert _week(horizon, WEEK_4).coverage == "empty"


def test_coverage_planned_always_agrees_with_the_planned_flag(db):
    user = _seed_user(db)
    plan = _seed_plan(db, user, horizon_end=WEEK_1 + timedelta(days=6))
    _seed_session(db, plan, start=WEEK_0, target_effort_score=60.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    for week in horizon.weeks:
        assert week.planned == (week.coverage == "planned")


def test_a_committed_session_past_the_recorded_reach_is_never_beyond_plan(db):
    """#848: `horizon_end` is written once, at draft time, and is not a DB
    constraint on what a `PlannedSession` can later say — a correction, a
    disagreeing writer, or (as here) a directly-constructed row can leave a
    committed session in a week later than the plan's own recorded reach. The
    view draws a single "Plan ends here" divider on the assumption that once a
    week reads `beyond_plan` every later week does too; a `planned` week
    surfacing after that divider would be the exact violation #848 named — a
    week with real sessions rendered as past the end of a plan that plainly
    still covers it. `_last_covered_week` must extend to cover it, not just the
    committed week itself but every INTERIOR week between the recorded reach
    and it, so none of them reads `beyond_plan` either.

    This state is not reachable through drafting (`draft.py` derives
    `horizon_end` from the weeks it writes), so it is constructed directly,
    exactly as the adversarial review that filed the issue did.
    """
    user = _seed_user(db)
    # The plan claims to reach only WEEK_1, but a committed session two weeks
    # later says otherwise.
    plan = _seed_plan(db, user, horizon_end=WEEK_1 + timedelta(days=6))
    _seed_session(db, plan, start=WEEK_3, target_effort_score=60.0)

    horizon = build_horizon(db, user, weeks=5, today=TODAY)

    assert _week(horizon, WEEK_0).coverage == "empty"
    # WEEK_1 and WEEK_2 are interior gaps now, not `beyond_plan`: the plan's
    # true reach (derived from what it actually holds) extends to WEEK_3.
    assert _week(horizon, WEEK_1).coverage == "empty"
    assert _week(horizon, WEEK_2).coverage == "empty"
    assert _week(horizon, WEEK_3).coverage == "planned"
    # Only the week genuinely past the plan's true reach reads beyond_plan.
    assert _week(horizon, WEEK_4).coverage == "beyond_plan"

    # The invariant the divider depends on, checked directly: no `planned` or
    # `sketched` week ever follows a `beyond_plan` one.
    seen_beyond_plan = False
    for week in horizon.weeks:
        if week.coverage == "beyond_plan":
            seen_beyond_plan = True
        elif seen_beyond_plan:
            assert week.coverage not in ("planned", "sketched"), (
                f"{week.week_start} is {week.coverage} after a beyond_plan week"
            )


def test_a_sketched_week_past_the_recorded_reach_is_never_beyond_plan(db):
    """Same #848 guarantee, for a week shape rather than a committed session."""
    user = _seed_user(db)
    plan = _seed_plan(
        db,
        user,
        horizon_end=WEEK_0 + timedelta(days=6),
        week_shapes=[
            {"week_start": WEEK_2.isoformat(), "phase": "build", "target_effort_score": 300}
        ],
    )
    _seed_session(db, plan, start=WEEK_0, target_effort_score=60.0)

    horizon = build_horizon(db, user, weeks=4, today=TODAY)

    assert _week(horizon, WEEK_0).coverage == "planned"
    assert _week(horizon, WEEK_1).coverage == "empty"
    assert _week(horizon, WEEK_2).coverage == "sketched"
    assert _week(horizon, WEEK_3).coverage == "beyond_plan"


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
