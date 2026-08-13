"""#830: the schedule API — the week, the horizon, and the runner's own races.

Read-only over the plan itself (the plan is written by the coach, not by a form);
the one thing this router writes is the goal race, because the race is the
runner's to state. This file pins the router-level kill switch, the two read
happy paths, the race CRUD, and the tenant boundary — a cross-tenant id is
indistinguishable from a missing one.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import User, UserProfile
from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan

FUTURE = date.today() + timedelta(days=60)
PAST = date.today() - timedelta(days=60)


def _act_as(user):
    """Inject the acting user (the resolution itself is covered elsewhere)."""
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


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


def _seed_race(db, user: User, *, race_date: date, name: str = "Autumn half") -> GoalRace:
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


# --- the kill switch -------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/schedule/week"),
        ("get", "/api/schedule/horizon"),
        ("get", "/api/schedule/races"),
        ("post", "/api/schedule/races"),
        ("delete", f"/api/schedule/races/{uuid4()}"),
    ],
)
def test_every_route_refuses_while_the_schedule_is_switched_off(
    db, client, monkeypatch, method, path
):
    """The switch is a ROUTER-level dependency, so a route added later cannot
    forget it — and it answers before any resource is even looked up."""
    _act_as(_seed_user(db))
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    resp = getattr(client, method)(
        path,
        **({"json": {"name": "x", "race_date": FUTURE.isoformat(), "distance_m": 10000}}
           if method == "post" else {}),
    )

    assert resp.status_code == 503
    assert "schedule" in resp.json()["detail"].lower()


def test_the_switch_being_on_is_the_default(db, client):
    _act_as(_seed_user(db))

    assert settings.SCHEDULE_ENABLED is True
    assert client.get("/api/schedule/week").status_code == 200


# --- the reads -------------------------------------------------------------


def test_get_week_serves_the_current_week(db, client):
    user = _seed_user(db)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.add(
        PlannedSession(
            plan_id=plan.id,
            user_id=user.id,
            window_start=date.today(),
            window_end=date.today() + timedelta(days=2),
            intent="easy",
            discipline="run",
            commitment="committed",
            title="Easy run",
            target_distance_m=8000,
        )
    )
    db.commit()
    _act_as(user)

    resp = client.get("/api/schedule/week")

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_plan"] is True
    assert body["is_current_week"] is True
    assert [s["title"] for s in body["sessions"]] == ["Easy run"]
    assert body["sessions"][0]["placement"] == "window"
    assert body["norm"] is not None


def test_get_week_accepts_any_day_of_the_week_to_read(db, client):
    _act_as(_seed_user(db))
    a_past_day = date.today() - timedelta(days=10)

    resp = client.get("/api/schedule/week", params={"week_start": a_past_day.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_current_week"] is False
    assert body["norm"] is None
    assert date.fromisoformat(body["week_start"]) <= a_past_day
    assert date.fromisoformat(body["week_end"]) >= a_past_day


def test_get_horizon_serves_a_continuous_run_of_weeks(db, client):
    _act_as(_seed_user(db))

    resp = client.get("/api/schedule/horizon", params={"weeks": 8})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["weeks"]) == 8
    assert body["weeks"][0]["is_current"] is True
    assert body["has_plan"] is False


def test_get_horizon_rejects_a_window_outside_its_bounds(db, client):
    _act_as(_seed_user(db))

    assert client.get("/api/schedule/horizon", params={"weeks": 0}).status_code == 422
    assert client.get("/api/schedule/horizon", params={"weeks": 53}).status_code == 422


# --- goal races ------------------------------------------------------------


def test_the_runner_states_their_own_race(db, client):
    user = _seed_user(db)
    _act_as(user)

    resp = client.post(
        "/api/schedule/races",
        json={
            "name": "Autumn half",
            "race_date": FUTURE.isoformat(),
            "distance_m": 21097.5,
            "priority": "A",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Autumn half"
    assert body["distance_m"] == 21097.5
    stored = db.query(GoalRace).filter(GoalRace.id == UUID(body["id"])).one()
    assert stored.user_id == user.id


def test_listing_races_is_future_only_unless_the_past_is_asked_for(db, client):
    user = _seed_user(db)
    _seed_race(db, user, race_date=FUTURE, name="ahead")
    _seed_race(db, user, race_date=PAST, name="behind")
    _act_as(user)

    ahead = client.get("/api/schedule/races")
    everything = client.get("/api/schedule/races", params={"include_past": "true"})

    assert [r["name"] for r in ahead.json()] == ["ahead"]
    assert [r["name"] for r in everything.json()] == ["behind", "ahead"]


def test_a_race_can_be_removed(db, client):
    user = _seed_user(db)
    race = _seed_race(db, user, race_date=FUTURE)
    _act_as(user)

    resp = client.delete(f"/api/schedule/races/{race.id}")

    assert resp.status_code == 204
    assert db.query(GoalRace).filter(GoalRace.id == race.id).first() is None


def test_deleting_a_race_leaves_the_plan_it_anchored_alive(db, client):
    """A plan outliving its race keeps its sessions and its shape; it simply
    stops being anchored. Throwing away weeks of work because the runner
    corrected a date would be the wrong trade."""
    user = _seed_user(db)
    race = _seed_race(db, user, race_date=FUTURE)
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        goal_race_id=race.id,
        rules=[],
        week_shapes=[],
    )
    db.add(plan)
    db.commit()
    _act_as(user)

    resp = client.delete(f"/api/schedule/races/{race.id}")

    assert resp.status_code == 204
    db.expire_all()
    survivor = db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one()
    assert survivor.status == "active"
    assert survivor.goal_race_id is None


def test_another_runners_race_is_indistinguishable_from_a_missing_one(db, client):
    theirs = _seed_race(db, _seed_user(db), race_date=FUTURE, name="not mine")
    _act_as(_seed_user(db))

    cross_tenant = client.delete(f"/api/schedule/races/{theirs.id}")
    absent = client.delete(f"/api/schedule/races/{uuid4()}")

    assert cross_tenant.status_code == 404
    assert absent.status_code == 404
    assert cross_tenant.json() == absent.json()
    assert db.query(GoalRace).filter(GoalRace.id == theirs.id).first() is not None


def test_another_runners_races_never_appear_in_the_list(db, client):
    theirs = _seed_user(db)
    _seed_race(db, theirs, race_date=FUTURE, name="not mine")
    mine = _seed_user(db)
    _seed_race(db, mine, race_date=FUTURE, name="mine")
    _act_as(mine)

    assert [r["name"] for r in client.get("/api/schedule/races").json()] == ["mine"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Zero distance", "race_date": "2026-12-01", "distance_m": 0},
        {"name": "Negative distance", "race_date": "2026-12-01", "distance_m": -5000},
        {
            "name": "Unknown priority",
            "race_date": "2026-12-01",
            "distance_m": 10000,
            "priority": "S",
        },
        {"name": "", "race_date": "2026-12-01", "distance_m": 10000},
        {"race_date": "2026-12-01", "distance_m": 10000},
        {
            "name": "Extra field",
            "race_date": "2026-12-01",
            "distance_m": 10000,
            "goal_time_s": 5400,
        },
    ],
)
def test_an_off_contract_race_is_rejected_at_the_boundary(db, client, payload):
    _act_as(_seed_user(db))

    resp = client.post("/api/schedule/races", json=payload)

    assert resp.status_code == 422
    assert db.query(GoalRace).count() == 0


def test_a_race_being_run_today_is_still_listed(db, client):
    """The "future" cut has a day of grace.

    There is no stored runner timezone to resolve a local day from, so a strict
    server `today` dropped a race being run TODAY for any runner west of the
    server. Erring a day wide shows one stale race; erring narrow hides the one
    the plan is built around.
    """
    user = _seed_user(db)
    _act_as(user)
    _seed_race(db, user, race_date=date.today(), name="Today's race")
    _seed_race(db, user, race_date=date.today() - timedelta(days=1), name="Yesterday")
    _seed_race(db, user, race_date=date.today() - timedelta(days=3), name="Last week")

    names = [r["name"] for r in client.get("/api/schedule/races").json()]

    # The grace day is the point: a strict server `today` cut would drop a race
    # the runner is still on the morning of. Anything older stays out.
    assert "Today's race" in names
    assert "Yesterday" in names
    assert "Last week" not in names


# --- the plan records the race it was built for (#884) ----------------------


def test_a_plan_drafted_with_a_race_stated_records_that_race(db):
    """`TrainingPlan.goal_race_id` existed from the start and nothing ever wrote
    it, so every plan in production carried a null pointer.

    Nothing looked visibly broken — drafting reads the runner's races separately
    and the horizon draws its marker the same way — but the plan did not know
    what it was FOR: a runner with two races had no way to tell which block
    belonged to which, and `delete_goal_race`'s detach could never fire.
    """
    from app.services.schedule import store

    user = _seed_user(db)
    race = store.create_goal_race(
        db, user.id, name="Autumn half", race_date=date.today() + timedelta(days=60),
        distance_m=21097.5, priority="A",
    )

    plan = store.create_drafting_plan(db, user.id)

    assert plan.goal_race_id == race.id


def test_the_block_is_built_for_the_runners_own_A_race_not_the_nearest_one(db):
    """`A` is the runner's word for the race the block is built for. A parkrun
    three weeks out does not restructure a marathon build, so priority wins over
    proximity — the ranking is theirs, not the app's."""
    from app.services.schedule import store

    user = _seed_user(db)
    store.create_goal_race(
        db, user.id, name="Local 5k", race_date=date.today() + timedelta(days=21),
        distance_m=5000, priority="C",
    )
    marathon = store.create_goal_race(
        db, user.id, name="Autumn marathon",
        race_date=date.today() + timedelta(days=90), distance_m=42195, priority="A",
    )

    plan = store.create_drafting_plan(db, user.id)

    assert plan.goal_race_id == marathon.id


def test_a_race_already_run_is_not_what_the_next_block_is_for(db):
    """A plan drafted after a race is for what comes next, not for the finish
    line behind it."""
    from app.services.schedule import store

    user = _seed_user(db)
    store.create_goal_race(
        db, user.id, name="Last month's half",
        race_date=date.today() - timedelta(days=30), distance_m=21097.5, priority="A",
    )

    plan = store.create_drafting_plan(db, user.id)

    assert plan.goal_race_id is None


def test_a_runner_with_no_race_drafts_a_plan_for_no_race(db):
    """Free progression is a destination, not a missing pointer."""
    from app.services.schedule import store

    plan = store.create_drafting_plan(db, _seed_user(db).id)

    assert plan.goal_race_id is None


def test_deleting_the_race_detaches_the_plan_that_was_built_for_it(db):
    """The detach existed and could never fire, because nothing ever attached.
    Deleting a race must not take the training with it."""
    from app.services.schedule import store

    user = _seed_user(db)
    race = store.create_goal_race(
        db, user.id, name="Autumn half", race_date=date.today() + timedelta(days=60),
        distance_m=21097.5, priority="A",
    )
    plan = store.create_drafting_plan(db, user.id)
    assert plan.goal_race_id == race.id

    store.delete_goal_race(db, race)
    db.refresh(plan)

    assert plan.goal_race_id is None
    assert db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one() is not None


def test_another_runners_race_is_never_what_my_plan_is_built_for(db):
    """The resolution is owner-scoped like every other read in this store."""
    from app.services.schedule import store

    stranger = _seed_user(db)
    store.create_goal_race(
        db, stranger.id, name="Their race", race_date=date.today() + timedelta(days=30),
        distance_m=10000, priority="A",
    )
    mine = _seed_user(db)

    plan = store.create_drafting_plan(db, mine.id)

    assert plan.goal_race_id is None
