"""#876: the backfill that fills in an interval session's edges, and abstains.

The point of this script is what it REFUSES to do. Sessions written before #876
state their warm-up in the detail as minutes, and the whole reason the warm-up
was missing in the first place is that a duration is not a distance until a pace
is assumed. So the abstention is the load-bearing behaviour here, not the happy
path -- a backfill that quietly estimated would put invented distance into the
runner's own plan, which is worse than the undercount it was fixing.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

from app.models import User
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from scripts.backfill_session_edges import candidates, propose, run

MON = date(2026, 8, 10)
INTERVALS = {"reps_planned": 6, "rep_distance_m": 400.0, "rest_s": 90.0}


def _user(db) -> User:
    user = User(email=f"bf-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _plan(db, user, *, status="active") -> TrainingPlan:
    plan = TrainingPlan(user_id=user.id, status=status, rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _session(db, plan, **kw) -> PlannedSession:
    payload = {
        "window_start": MON + timedelta(days=2),
        "window_end": MON + timedelta(days=2),
        "intent": "quality",
        "discipline": "run",
        "commitment": "committed",
        "title": "Intervals: 6x400m",
        "structure": dict(INTERVALS),
    }
    payload.update(kw)
    session = PlannedSession(plan_id=plan.id, user_id=plan.user_id, **payload)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# --- what it refuses to do -------------------------------------------------


def test_a_warmup_written_as_minutes_is_abstained_on_never_estimated(db):
    """The whole reason #876 happened, and the one thing this must not do."""
    plan = _plan(db, _user(db))
    row = _session(
        db,
        plan,
        detail=(
            "Warm up 10 min easy. 6 reps of 400m at 4:30/km with 90 sec standing "
            "recovery between reps. Cool down 10 min easy."
        ),
    )

    assert propose(row) == (None, None)

    written = run(db, today=MON, apply=True, overrides={})
    db.refresh(row)

    assert written == 0
    assert "warmup_distance_m" not in row.structure


def test_a_rep_distance_in_a_neighbouring_sentence_is_not_read_as_the_warmup(db):
    """Sentence-scoped: "6 reps of 400m" must not become a 400 m warm-up."""
    plan = _plan(db, _user(db))
    row = _session(
        db, plan, detail="Warm up easy. 6 reps of 400m at 4:30/km. Cool down easy."
    )

    assert propose(row) == (None, None)


# --- what it does do -------------------------------------------------------


def test_a_warmup_written_as_a_distance_is_read_and_written(db):
    plan = _plan(db, _user(db))
    row = _session(
        db,
        plan,
        detail="Warm up 1.1 km easy. 6 reps of 400m at 4:30/km. Cool down 1000 m easy.",
    )

    assert propose(row) == (1100.0, 1000.0)

    run(db, today=MON, apply=True, overrides={})
    db.refresh(row)

    assert row.structure["warmup_distance_m"] == 1100.0
    assert row.structure["cooldown_distance_m"] == 1000.0
    # The session the runner agreed: 1.1 + 2.4 + 1.0.
    total = (
        row.structure["warmup_distance_m"]
        + row.structure["reps_planned"] * row.structure["rep_distance_m"]
        + row.structure["cooldown_distance_m"]
    )
    assert total == 4500.0


def test_an_explicit_set_supplies_what_the_detail_cannot(db):
    """The abstain path's way out: a human decides, the script writes it."""
    plan = _plan(db, _user(db))
    row = _session(db, plan, detail="Warm up 10 min easy. Cool down 10 min easy.")

    run(db, today=MON, apply=True, overrides={row.id: (1100.0, 1000.0)})
    db.refresh(row)

    assert row.structure["warmup_distance_m"] == 1100.0
    assert row.structure["cooldown_distance_m"] == 1000.0


def test_a_dry_run_writes_nothing(db):
    plan = _plan(db, _user(db))
    row = _session(db, plan, detail="Warm up 1.1 km easy. Cool down 1 km easy.")

    written = run(db, today=MON, apply=False, overrides={})
    db.refresh(row)

    assert written == 0
    assert "warmup_distance_m" not in row.structure


# --- scope and idempotence -------------------------------------------------


def test_it_leaves_alone_what_is_not_its_business(db):
    """History, superseded plans, easy runs, and sessions stating a total.

    Each is excluded for its own reason: a past session's planned distance is no
    longer read, a superseded plan is history, an easy run has no edges to fill,
    and a session carrying `target_distance_m` already states the whole of itself
    -- writing edges under it would put two answers on one session.
    """
    user = _user(db)
    plan = _plan(db, user)
    detail = "Warm up 1.1 km easy. Cool down 1 km easy."

    past = _session(
        db, plan, detail=detail,
        window_start=MON - timedelta(days=7), window_end=MON - timedelta(days=7),
    )
    superseded = _session(db, _plan(db, user, status="superseded"), detail=detail)
    easy = _session(db, plan, detail=detail, intent="easy", structure=None)
    stated = _session(db, plan, detail=detail, target_distance_m=4500)

    picked = {row.id for row in candidates(db, today=MON)}

    assert picked == set()
    for row in (past, superseded, easy, stated):
        assert row.id not in picked


def test_it_is_idempotent_and_never_overwrites_an_existing_edge(db):
    plan = _plan(db, _user(db))
    row = _session(
        db,
        plan,
        detail="Warm up 1.1 km easy. Cool down 1 km easy.",
        structure={**INTERVALS, "warmup_distance_m": 900.0},
    )

    run(db, today=MON, apply=True, overrides={})
    db.refresh(row)

    # The stored warm-up stands; only the missing cool-down is filled.
    assert row.structure["warmup_distance_m"] == 900.0
    assert row.structure["cooldown_distance_m"] == 1000.0

    # Second run has nothing left to do.
    assert run(db, today=MON, apply=True, overrides={}) == 0
