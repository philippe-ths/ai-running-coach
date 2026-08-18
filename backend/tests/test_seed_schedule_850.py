"""The prod seed reaches the schedule (#850).

`make seed-local` is the supported offline real-data path, and until now it
copied no goal race, no training plan and no planned session, so a seeded
runner always landed in free mode and the schedule screen could not be reached
at all without hand-building rows.

These tests drive `seed()` directly against two throwaway SQLite databases,
with foreign keys ENFORCED, because the interesting case is a completed
session whose completing run fell outside the `--activities` window: without
the nulling in `seed()` that row is a dangling FK, and an unenforced SQLite
would let it through and prove nothing.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Activity,
    GoalRace,
    PlannedSession,
    TrainingPlan,
    User,
)

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_from_prod.py"


def _load_seed_module():
    """`backend/scripts/` is not a package, so load the file by path."""
    spec = importlib.util.spec_from_file_location("seed_from_prod_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def enforce_sqlite_fks():
    """SQLite ignores foreign keys unless asked. The dangling-pointer case is
    the whole point of this file, so ask."""

    def _on_connect(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(Engine, "connect", _on_connect)
    try:
        yield
    finally:
        event.remove(Engine, "connect", _on_connect)


def _url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def _build_source(url: str, *, with_schedule_tables: bool = True) -> dict:
    """A runner with three activities, a race, a plan and three sessions.

    The three sessions are the three cases: never completed, completed by an
    activity the window keeps, completed by an activity the window drops.
    """
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    if not with_schedule_tables:
        for model in (PlannedSession, TrainingPlan, GoalRace):
            model.__table__.drop(engine)

    now = datetime.now(timezone.utc)
    ids: dict = {}
    with Session(engine) as db:
        user = User(id=uuid.uuid4(), email="runner@example.com")
        db.add(user)
        db.flush()

        # Newest first: the window of 2 keeps `recent` and `middle`, drops `old`.
        activities = {}
        for offset, label in ((1, "recent"), (5, "middle"), (30, "old")):
            act = Activity(
                id=uuid.uuid4(),
                user_id=user.id,
                strava_activity_id=1000 + offset,
                start_date=now - timedelta(days=offset),
                type="Run",
                name=f"{label} run",
                distance_m=10_000,
                moving_time_s=3_000,
                elapsed_time_s=3_100,
            )
            db.add(act)
            activities[label] = act
        db.flush()
        ids["activities"] = {k: v.id for k, v in activities.items()}

        if with_schedule_tables:
            race = GoalRace(
                id=uuid.uuid4(),
                user_id=user.id,
                name="Spring Half",
                race_date=date.today() + timedelta(days=60),
                distance_m=21097.5,
                priority="A",
            )
            db.add(race)
            db.flush()
            plan = TrainingPlan(
                id=uuid.uuid4(),
                user_id=user.id,
                status="active",
                goal_race_id=race.id,
                horizon_end=date.today() + timedelta(days=84),
                rules=[{"kind": "rest_day_after", "intent": "intervals"}],
                week_shapes=[{"week_start": str(date.today()), "phase": "Build"}],
            )
            db.add(plan)
            db.flush()

            sessions = {
                "never_completed": None,
                "completed_by_kept": activities["recent"],
                "completed_by_dropped": activities["old"],
            }
            ids["sessions"] = {}
            for label, completing in sessions.items():
                session = PlannedSession(
                    id=uuid.uuid4(),
                    plan_id=plan.id,
                    user_id=user.id,
                    window_start=date.today(),
                    window_end=date.today() + timedelta(days=2),
                    intent="easy",
                    discipline="run",
                    commitment="committed",
                    title=label,
                    target_distance_m=10_000,
                    completed_at=None if completing is None else now,
                    completed_activity_id=None if completing is None else completing.id,
                    completion_source=None if completing is None else "AUTO",
                )
                db.add(session)
                db.flush()
                ids["sessions"][label] = session.id
            ids["race"] = race.id
            ids["plan"] = plan.id
        db.commit()
    engine.dispose()
    return ids


def _target(url: str) -> Engine:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


def test_a_seeded_database_reproduces_the_schedule(tmp_path, enforce_sqlite_fks):
    """The first acceptance criterion: a runner with a plan in the source has
    one locally, race and rules and sessions included."""
    seed_mod = _load_seed_module()
    source_url, target_url = _url(tmp_path, "src.db"), _url(tmp_path, "dst.db")
    ids = _build_source(source_url)
    target = _target(target_url)

    seed_mod.seed(source_url, target_url, activities_limit=2, with_live_tokens=False)

    with Session(target) as db:
        races = db.execute(select(GoalRace)).scalars().all()
        plans = db.execute(select(TrainingPlan)).scalars().all()
        sessions = db.execute(select(PlannedSession)).scalars().all()

        assert [r.id for r in races] == [ids["race"]]
        assert races[0].distance_m == pytest.approx(21097.5)
        assert [p.id for p in plans] == [ids["plan"]]
        # The plan keeps its race pointer and its two JSON columns.
        assert plans[0].goal_race_id == ids["race"]
        assert plans[0].rules == [{"kind": "rest_day_after", "intent": "intervals"}]
        assert plans[0].week_shapes and plans[0].week_shapes[0]["phase"] == "Build"
        # Every session survives the window, including the one no run completed.
        assert {s.title for s in sessions} == {
            "never_completed",
            "completed_by_kept",
            "completed_by_dropped",
        }
    target.dispose()


def test_a_completion_outside_the_window_keeps_the_session_and_drops_the_link(
    tmp_path, enforce_sqlite_fks
):
    """A planned session is not a child of an activity. When the run that
    completed it falls outside `--activities`, the session and the fact of its
    completion survive; only the pointer goes."""
    seed_mod = _load_seed_module()
    source_url, target_url = _url(tmp_path, "src.db"), _url(tmp_path, "dst.db")
    ids = _build_source(source_url)
    target = _target(target_url)

    seed_mod.seed(source_url, target_url, activities_limit=2, with_live_tokens=False)

    with Session(target) as db:
        by_title = {
            s.title: s for s in db.execute(select(PlannedSession)).scalars().all()
        }
        kept_activity_ids = set(db.execute(select(Activity.id)).scalars().all())

        dropped = by_title["completed_by_dropped"]
        assert dropped.completed_activity_id is None
        # The completion itself is NOT unsaid: the runner did the session.
        assert dropped.completed_at is not None
        assert dropped.completion_source == "AUTO"

        kept = by_title["completed_by_kept"]
        assert kept.completed_activity_id == ids["activities"]["recent"]
        assert kept.completed_activity_id in kept_activity_ids

        assert by_title["never_completed"].completed_activity_id is None
        assert by_title["never_completed"].completed_at is None
    target.dispose()


def test_a_source_without_the_schedule_tables_still_seeds(tmp_path, enforce_sqlite_fks):
    """The second acceptance criterion, and the reason production seeds today:
    prod holds no schedule tables until it runs the migration, and a branch
    that adds one must still seed from an un-migrated source."""
    seed_mod = _load_seed_module()
    source_url, target_url = _url(tmp_path, "src.db"), _url(tmp_path, "dst.db")
    _build_source(source_url, with_schedule_tables=False)
    target = _target(target_url)

    seed_mod.seed(source_url, target_url, activities_limit=2, with_live_tokens=False)

    with Session(target) as db:
        assert db.execute(select(Activity)).scalars().all()
        assert db.execute(select(GoalRace)).scalars().all() == []
        assert db.execute(select(TrainingPlan)).scalars().all() == []
        assert db.execute(select(PlannedSession)).scalars().all() == []
    target.dispose()
