"""#1064: GET and POST /api/schedule/season.

NO TEST HERE MAY REACH REDIS OR THE NETWORK: the enqueue seam is replaced.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.schedule import season_store, store
from app.services.weeks import week_start
from tests._season_fixtures_1064 import standard_goals, valid_payload


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    """Nothing here may touch Redis; the recorder doubles as the assertion."""
    seen = []
    monkeypatch.setattr("app.api.schedule.enqueue_season", lambda uid, sid: seen.append((uid, sid)))
    return seen


def _user(db, email=None):
    user = User(email=email or f"season-api-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general", experience_level="intermediate",
                       weekly_days_available=4, max_hr=190))
    db.commit()
    db.refresh(user)
    return user


def _goals(db, user):
    """Goals dated relative to the REAL today, so they stay upcoming."""
    today = date.today()
    rows = {}
    for key, g in standard_goals().items():
        rows[key] = store.create_goal_race(
            db, user.id, name=g.name, booked=g.booked, priority=g.priority,
            race_date=today + timedelta(days=30) if key == "race" else None,
            window_start=today + timedelta(days=200) if key in ("challenge", "marathon") else None,
            window_end=today + timedelta(days=260) if key in ("challenge", "marathon") else None,
            distance_m=g.distance_m,
        )
    return rows


def _active_season(db, user, goals, *, challenge_start):
    season = season_store.create_drafting_season(db, user.id)
    payload = valid_payload(goals)
    race_day = goals["race"].race_date
    payload["goals"][0]["date"] = race_day.isoformat()
    payload["goals"][1]["challenge"]["start"] = challenge_start.isoformat()
    payload["goals"][1]["challenge"]["weeks"] = 4
    payload["goals"][1]["challenge"]["at_least"] = 7200
    payload["goals"][2]["window_start"] = goals["marathon"].window_start.isoformat()
    payload["goals"][2]["window_end"] = goals["marathon"].window_end.isoformat()
    payload["phases"] = [
        {"kind": "build", "start": date.today().isoformat(),
         "end": (race_day - timedelta(days=1)).isoformat()},
        {"kind": "race", "start": race_day.isoformat(), "end": race_day.isoformat(),
         "goal_id": str(goals["race"].id)},
        {"kind": "base", "start": (race_day + timedelta(days=1)).isoformat(),
         "end": goals["marathon"].window_end.isoformat()},
    ]
    season.plan = payload
    season.goals_fingerprint = season_store.goals_fingerprint(
        store.list_goal_races(db, user.id, on_or_after=date.today())
    )
    season.model_id = "claude-opus-5-5"
    season_store.activate_season(db, season)
    return season


# --- the kill switch ---------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_the_season_routes_refuse_while_the_schedule_is_switched_off(db, client, monkeypatch, method):
    _act_as(_user(db))
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
    assert getattr(client, method)("/api/schedule/season").status_code == 503


# --- reading -----------------------------------------------------------------


def test_with_no_season_the_read_says_so_and_still_lists_the_goals(db, client):
    user = _user(db)
    _goals(db, user)
    _act_as(user)

    body = client.get("/api/schedule/season").json()

    assert body["status"] is None and body["plan"] is None
    assert "no season yet" in body["message"]
    assert len(body["goals"]) == 4


def test_an_active_season_is_served_with_its_plan_goals_and_challenge_status(db, client):
    user = _user(db)
    goals = _goals(db, user)
    this_week = week_start(date.today(), 0)
    start = this_week - timedelta(weeks=2)
    _active_season(db, user, goals, challenge_start=start)
    # One measured week inside the challenge: 8100 s at zone 2+ against 7200 s asked.
    when = datetime.combine(start + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    activity = Activity(
        user_id=user.id, strava_activity_id=99, start_date=when, type="Run", name="r",
        distance_m=10000, moving_time_s=9000, elapsed_time_s=9000, elev_gain_m=0.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[], confidence="medium",
        confidence_reasons=[], time_in_zones={"Z1": 900, "Z2": 8100},
    ))
    db.commit()
    _act_as(user)

    body = client.get("/api/schedule/season").json()

    assert body["status"] == "active" and body["model_id"] == "claude-opus-5-5"
    assert body["plan"]["goals"][1]["kind"] == "challenge"
    assert body["stale"] is False and body["regenerating"] is False
    (status,) = body["challenges"]
    assert status["name"] == "10h a week"
    assert [w["index"] for w in status["weeks"]] == [1, 2, 3, 4]
    assert status["weeks"][0]["actual"] == 8100 and status["weeks"][0]["met"] is True
    assert status["weeks"][1]["met"] is False
    assert status["weeks"][2]["met"] is None  # the week in progress
    assert status["streak"] == 1
    assert len(body["goals"]) == 4


def test_editing_a_goal_after_the_season_marks_it_stale(db, client):
    user = _user(db)
    goals = _goals(db, user)
    _active_season(db, user, goals, challenge_start=week_start(date.today(), 0) + timedelta(weeks=3))
    _act_as(user)
    assert client.get("/api/schedule/season").json()["stale"] is False

    store.update_goal_race(db, goals["marathon"], notes="changed my mind")

    assert client.get("/api/schedule/season").json()["stale"] is True


def test_a_season_being_rewritten_is_served_with_the_flag_not_replaced(db, client):
    user = _user(db)
    goals = _goals(db, user)
    active = _active_season(db, user, goals, challenge_start=week_start(date.today(), 0) + timedelta(weeks=3))
    season_store.create_drafting_season(db, user.id)
    _act_as(user)

    body = client.get("/api/schedule/season").json()

    assert body["status"] == "active" and body["id"] == str(active.id)
    assert body["regenerating"] is True and body["plan"] is not None


def test_a_failed_first_attempt_reports_the_runner_facing_message(db, client):
    user = _user(db)
    season = season_store.create_drafting_season(db, user.id)
    season_store.fail_season(db, season, season_store.FAILURE_MESSAGE)
    _act_as(user)

    body = client.get("/api/schedule/season").json()

    assert body["status"] == "failed" and body["message"] == season_store.FAILURE_MESSAGE
    assert body["plan"] is None


def test_a_drafting_season_past_its_deadline_reads_as_failed(db, client):
    user = _user(db)
    season = season_store.create_drafting_season(db, user.id)
    season.created_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.RQ_JOB_TIMEOUT_SECONDS + 181
    )
    db.commit()
    _act_as(user)

    assert client.get("/api/schedule/season").json()["status"] == "failed"


def test_another_runners_season_is_never_served(db, client):
    owner, other = _user(db), _user(db)
    goals = _goals(db, owner)
    _active_season(db, owner, goals, challenge_start=week_start(date.today(), 0) + timedelta(weeks=3))
    _act_as(other)

    body = client.get("/api/schedule/season").json()

    assert body["status"] is None and body["plan"] is None and body["goals"] == []


# --- asking ------------------------------------------------------------------


def test_asking_returns_202_leaves_a_drafting_row_and_enqueues_it(db, client, enqueued):
    user = _user(db)
    _goals(db, user)
    _act_as(user)

    resp = client.post("/api/schedule/season")

    assert resp.status_code == 202
    assert resp.json()["status"] == "drafting"
    season = season_store.latest_season(db, user.id)
    assert season.status == season_store.DRAFTING
    assert enqueued == [(user.id, season.id)]


def test_a_second_ask_while_one_is_drafting_is_a_409_and_writes_nothing(db, client, enqueued):
    user = _user(db)
    _goals(db, user)
    _act_as(user)
    assert client.post("/api/schedule/season").status_code == 202

    resp = client.post("/api/schedule/season")

    assert resp.status_code == 409
    assert len(enqueued) == 1
    from app.models.season import Season

    assert db.query(Season).filter(Season.user_id == user.id).count() == 1


def test_asking_with_no_goal_is_refused_without_spending_anything(db, client, enqueued):
    user = _user(db)
    _act_as(user)

    resp = client.post("/api/schedule/season")

    assert resp.status_code == 422
    assert enqueued == []
