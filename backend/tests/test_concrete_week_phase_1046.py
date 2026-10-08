"""#1046: a concrete week keeps the phase the coach named for it.

The drafting tool asks for a phase on every concrete week, but concrete weeks are
stored as `PlannedSession` rows, which have no phase column, so the horizon read
"No phase" for exactly the weeks that matter most. The phase now rides in
`week_shapes` as a phase-only entry (no totals), which the horizon already reads
for a planned week. These tests pin the three things that has to hold: the phase
reaches the horizon, the phase-only entry changes no other reader's answer, and
an amendment keeps the amended week's own phase.

All row data is synthetic test setup (represents no real runner).
"""

from datetime import date, timedelta
from uuid import uuid4

from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.schemas.season import SeasonPlan
from app.services.schedule import store
from app.services.schedule.amend import AmendedPlan, _apply, _shape_lines
from app.services.schedule.coach_view import _written_through
from app.services.schedule.draft import _persist
from app.services.schedule.draft_contract import DraftedPlan, normalise
from app.services.schedule.effort import build_load_model
from app.services.schedule.frames import build_frames
from app.services.schedule.horizon import build_horizon

TODAY = date(2026, 8, 12)  # a Wednesday
WEEK_0 = date(2026, 8, 10)
WEEK_1 = WEEK_0 + timedelta(days=7)
WEEK_2 = WEEK_0 + timedelta(days=14)
WEEK_3 = WEEK_0 + timedelta(days=21)


def _user(db) -> User:
    user = User(email=f"phase-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _session(day: date, *, commitment="committed", title="Easy run") -> dict:
    return {
        "window_start": day.isoformat(),
        "window_end": day.isoformat(),
        "intent": "easy",
        "discipline": "run",
        "commitment": commitment,
        "title": title,
    }


def _week(week_start: date, *sessions: dict, phase=None) -> dict:
    return {
        "week_start": week_start.isoformat(),
        "phase": phase,
        "sessions": list(sessions),
    }


def _draft(weeks) -> DraftedPlan:
    return DraftedPlan.model_validate(
        normalise({"summary": "A plan.", "rules": [], "weeks": list(weeks)})
    )


def _peak_frames():
    """Frames for a season whose phase covers WEEK_2 (a shape, not concrete)."""
    season = SeasonPlan.model_validate(
        {
            "summary": "s",
            "goals": [],
            "phases": [
                {
                    "kind": "sharpen", "start": WEEK_2.isoformat(),
                    "end": (WEEK_2 + timedelta(days=6)).isoformat(), "run_km": 40,
                }
            ],
        }
    )
    return build_frames(
        season=season, goals=[], facts=[], starts_on=0, today=TODAY, horizon_weeks=4
    )


def _persisted(db, user, drafted, *, frames=()) -> TrainingPlan:
    plan = TrainingPlan(user_id=user.id, status="drafting", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    _persist(
        db, user, plan, drafted, build_load_model([], TODAY), model_id="m",
        frames=frames,
    )
    db.refresh(plan)
    return plan


def _horizon_week(db, user, week_start):
    horizon = build_horizon(db, user, weeks=6, today=TODAY)
    return next(w for w in horizon.weeks if w.week_start == week_start)


def test_a_drafted_concrete_weeks_phase_reaches_the_horizon(db):
    user = _user(db)
    _persisted(
        db,
        user,
        _draft(
            [
                _week(WEEK_0, _session(WEEK_0 + timedelta(days=2)), phase="Sharpen"),
                _week(WEEK_1, _session(WEEK_1 + timedelta(days=2)), phase="Taper"),
            ]
        ),
    )

    current = _horizon_week(db, user, WEEK_0)
    assert (current.coverage, current.phase) == ("planned", "Sharpen")
    assert _horizon_week(db, user, WEEK_1).phase == "Taper"


def test_a_phase_only_entry_changes_no_coverage_reach_or_count(db):
    user = _user(db)
    plan = _persisted(
        db,
        user,
        _draft(
            [
                _week(WEEK_0, _session(WEEK_0 + timedelta(days=2)), phase="Build"),
                # Suggestions only: not planned, so it must not turn into a sketch.
                _week(
                    WEEK_1,
                    _session(WEEK_1 + timedelta(days=2), commitment="suggested"),
                    phase="Build",
                ),
            ],
        ),
        frames=_peak_frames(),
    )

    shapes = store.plan_week_shapes(plan)
    assert {s.week_start for s in shapes} == {WEEK_0, WEEK_2}
    phase_only = next(s for s in shapes if s.week_start == WEEK_0)
    assert store.is_phase_only(phase_only)
    assert phase_only.target_running_distance_m is None

    horizon = build_horizon(db, user, weeks=6, today=TODAY)
    coverage = {w.week_start: w.coverage for w in horizon.weeks}
    assert coverage[WEEK_0] == "planned"
    assert coverage[WEEK_1] == "empty"  # suggestions alone stay empty
    assert coverage[WEEK_2] == "sketched"
    assert coverage[WEEK_3] == "beyond_plan"
    # The phase-only week states no totals of its own.
    assert _horizon_week(db, user, WEEK_0).quality_focus is None

    # Only the genuinely sketched week is "still only shape".
    out = _written_through(db, user.id, plan, TODAY, 0)
    assert out["weeks_still_only_shape"] == 1
    assert out["sessions_written_through"] == (WEEK_0 + timedelta(days=2)).isoformat()


def test_a_plan_stored_before_this_change_still_reads_without_a_phase(db):
    user = _user(db)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.add(
        PlannedSession(
            plan_id=plan.id,
            user_id=user.id,
            window_start=WEEK_0 + timedelta(days=2),
            window_end=WEEK_0 + timedelta(days=2),
            intent="easy",
            discipline="run",
            commitment="committed",
            title="Easy",
        )
    )
    db.commit()

    week = _horizon_week(db, user, WEEK_0)
    assert (week.coverage, week.phase) == ("planned", None)


def _plan_with_shapes(db, user, shapes) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=shapes,
        horizon_end=WEEK_2 + timedelta(days=6),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _amend(db, user, plan, weeks):
    _apply(
        db,
        user,
        plan,
        AmendedPlan.model_validate({"weeks": weeks}),
        build_load_model([], TODAY),
        start=WEEK_1,
        end=WEEK_1 + timedelta(days=6),
        today=TODAY,
    )
    db.refresh(plan)


def test_an_amended_week_takes_the_amended_weeks_own_phase(db):
    user = _user(db)
    plan = _plan_with_shapes(
        db,
        user,
        [
            {"week_start": WEEK_1.isoformat(), "phase": "Build"},
            {
                "week_start": WEEK_2.isoformat(),
                "phase": "Peak",
                "long_run_distance_m": 20000.0,
            },
        ],
    )

    _amend(
        db, user, plan,
        [_week(WEEK_1, _session(WEEK_1 + timedelta(days=1)), phase="Recover")],
    )

    shapes = {s.week_start: s for s in store.plan_week_shapes(plan)}
    assert shapes[WEEK_1].phase == "Recover"
    assert store.is_phase_only(shapes[WEEK_1])
    # The week beyond the window keeps everything it had.
    assert shapes[WEEK_2].long_run_distance_m == 20000.0
    assert _horizon_week(db, user, WEEK_1).phase == "Recover"


def test_an_amended_week_with_no_phase_leaves_no_second_answer(db):
    user = _user(db)
    plan = _plan_with_shapes(
        db, user, [{"week_start": WEEK_1.isoformat(), "phase": "Build",
                    "long_run_distance_m": 18000.0}]
    )

    _amend(db, user, plan, [_week(WEEK_1, _session(WEEK_1 + timedelta(days=1)))])

    assert store.plan_week_shapes(plan) == []


def test_the_amend_context_does_not_present_a_phase_marker_as_an_agreed_shape(db):
    user = _user(db)
    plan = _plan_with_shapes(
        db,
        user,
        [
            {"week_start": WEEK_1.isoformat(), "phase": "Build"},
            {"week_start": WEEK_2.isoformat(), "phase": "Peak",
             "long_run_distance_m": 20000.0},
        ],
    )

    lines = _shape_lines(plan, WEEK_1, WEEK_2 + timedelta(days=6))

    assert len(lines) == 1
    assert "Peak" in lines[0] and "20.0 km" in lines[0]
