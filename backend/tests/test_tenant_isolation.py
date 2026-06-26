"""P2.1 tenant-isolation negative contract (#119).

The new security contract for multi-user: a request authenticated as user A must
never read or mutate user B's data. These tests seed two independent users with a
full data graph each, authenticate as one, and assert that the other's resources
are DENIED (404) -- not served. The existing single-user suite is the positive
oracle (behaviour preserved for the owning user); this file is the negative one.

Auth is injected by overriding verify_clerk_session (the resolution itself is
covered by test_clerk_auth); here we pin the QUERY scoping that rides on top.
"""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import (
    Activity,
    Block,
    CheckIn,
    DerivedMetric,
    StravaAccount,
    User,
    UserMaterial,
    UserProfile,
)
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport


def _seed_user(db, *, email, athlete_id, strava_activity_id, atype, hash_suffix):
    """A full per-user data graph: user, profile, Strava account, activity (+
    metric, report, chat), a block, and an uploaded material."""
    user = User(email=email)
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=athlete_id, access_token="t",
        refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type=atype, name=f"{atype} run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="easy", structure="continuous",
        duration_class="standard", effort_score=50.0, flags=[],
        confidence="medium", confidence_reasons=[],
    ))
    db.add(CoachReport(
        activity_id=activity.id,
        report={"message": "Nice run.", "headline": "ok", "next_steps": [],
                "risks": [], "questions": []},
        meta={"confidence": "medium", "model_id": "m", "prompt_id": "x",
              "schema_version": "2.0", "input_hash": "x",
              "generated_at": datetime.now(timezone.utc).isoformat(), "policy_violations": []},
        context_pack={}, prompt_id="x", schema_version="2.0", is_fallback=False,
    ))
    db.add(CoachChatMessage(activity_id=activity.id, role="user", content="hi"))
    block = Block(
        user_id=user.id, start_date=activity.start_date,
        end_date=activity.start_date + timedelta(seconds=1500),
        primary_activity_id=activity.id,
    )
    db.add(block)
    db.add(UserMaterial(
        user_id=user.id, kind="other", title="mine", filename="m.md",
        raw_text="private", content_hash=f"hash-{hash_suffix}", status="active",
    ))
    db.commit()
    db.refresh(activity)
    db.refresh(block)
    return SimpleNamespace(user=user, activity=activity, block=block)


@pytest.fixture
def two_users(db):
    a = _seed_user(db, email="a@test.dev", athlete_id=101, strava_activity_id=1001,
                   atype="Run", hash_suffix="a")
    b = _seed_user(db, email="b@test.dev", athlete_id=202, strava_activity_id=2002,
                   atype="Ride", hash_suffix="b")
    yield a, b


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


# --- reads -----------------------------------------------------------------

def test_list_returns_only_own_activities(client, two_users):
    a, b = two_users
    _act_as(a.user)
    resp = client.get("/api/activities")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert str(a.activity.id) in ids
    assert str(b.activity.id) not in ids


def test_detail_of_other_users_activity_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    assert client.get(f"/api/activities/{b.activity.id}").status_code == 404
    assert client.get(f"/api/activities/{a.activity.id}").status_code == 200


def test_coach_report_of_other_user_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    assert client.get(
        f"/api/activities/{b.activity.id}/coach-report?generate=false"
    ).status_code == 404
    assert client.get(
        f"/api/activities/{a.activity.id}/coach-report?generate=false"
    ).status_code == 200


def test_chat_history_of_other_user_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    assert client.get(
        f"/api/activities/{b.activity.id}/coach-chat"
    ).status_code == 404


def test_trends_types_scoped_to_own_activities(client, two_users):
    a, b = two_users
    _act_as(a.user)
    types = client.get("/api/trends/types").json()
    assert "Run" in types
    assert "Ride" not in types  # B's Ride must not leak


# --- writes ----------------------------------------------------------------

def test_intent_write_on_other_users_activity_is_404(client, two_users, db):
    a, b = two_users
    _act_as(a.user)
    resp = client.put(
        f"/api/activities/{b.activity.id}/intent", json={"user_intent": "hard"}
    )
    assert resp.status_code == 404
    db.refresh(b.activity)
    assert b.activity.user_intent != "hard"


def test_checkin_on_other_users_activity_is_404(client, two_users, db):
    a, b = two_users
    _act_as(a.user)
    resp = client.post(
        f"/api/activities/{b.activity.id}/checkin", json={"rpe": 9}
    )
    assert resp.status_code == 404
    assert db.query(CheckIn).filter(CheckIn.activity_id == b.activity.id).count() == 0


def test_process_deep_on_other_users_activity_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    assert client.post(
        f"/api/activities/{b.activity.id}/process_deep"
    ).status_code == 404


def test_regenerate_report_on_other_users_activity_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    assert client.post(
        f"/api/activities/{b.activity.id}/coach-report/regenerate"
    ).status_code == 404


def test_delete_chat_on_other_users_activity_is_404(client, two_users, db):
    a, b = two_users
    _act_as(a.user)
    resp = client.delete(f"/api/activities/{b.activity.id}/coach-chat")
    assert resp.status_code == 404
    # B's chat is intact.
    assert db.query(CoachChatMessage).filter(
        CoachChatMessage.activity_id == b.activity.id
    ).count() == 1


def test_post_chat_on_other_users_activity_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    resp = client.post(
        f"/api/activities/{b.activity.id}/coach-chat", json={"message": "hi"}
    )
    assert resp.status_code == 404


def test_block_split_on_other_users_block_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    resp = client.post(
        f"/api/blocks/{b.block.id}/split",
        json={"activity_id": str(b.activity.id)},
    )
    assert resp.status_code == 404


def test_block_merge_on_other_users_block_is_404(client, two_users):
    a, b = two_users
    _act_as(a.user)
    resp = client.post(
        f"/api/blocks/{a.block.id}/merge",
        json={"other_block_id": str(b.block.id)},
    )
    # A owns the path block but not the other; merging across tenants must deny.
    assert resp.status_code == 404


def test_material_of_other_user_is_404(client, two_users, db):
    a, b = two_users
    _act_as(a.user)
    b_material = db.query(UserMaterial).filter(UserMaterial.user_id == b.user.id).first()
    assert client.get(f"/api/coach/materials/{b_material.id}").status_code == 404


# --- trends + Strava-import read/maintenance endpoints (#503) ----------------
#
# Per-user scoping lives in the service layer (test_trends_user_scoping.py), but
# these endpoint-boundary tests pin that the scoping actually rides on the
# authenticated caller, paralleling test_trends_types_scoped_to_own_activities.


def _recent_activity(db, user_id, *, days_ago: int, distance_m: int, name: str) -> Activity:
    """An analysed activity dated relative to today, so it lands inside the
    rolling trends/load/volume windows regardless of when the suite runs."""
    on = date.today() - timedelta(days=days_ago)
    activity = Activity(
        user_id=user_id,
        strava_activity_id=int(uuid4().int % 1_000_000_000),
        start_date=datetime.combine(on, time(12, 0)),
        type="Run",
        name=name,
        distance_m=distance_m,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    db.add(DerivedMetric(
        activity_id=activity.id, effort_score=50.0, flags=[],
        confidence="high", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


def test_trends_load_scoped_to_own_activities(client, two_users, db):
    """GET /api/trends/load must never carry another user's activity contributions."""
    a, b = two_users
    a_act = _recent_activity(db, a.user.id, days_ago=2, distance_m=1000, name="A only")
    b_act = _recent_activity(db, b.user.id, days_ago=2, distance_m=9999, name="B only")

    _act_as(a.user)
    weeks = client.get("/api/trends/load").json()["weeks"]
    contrib_ids = {pt["id"] for w in weeks for pt in w["activities"]}
    assert str(a_act.id) in contrib_ids
    assert str(b_act.id) not in contrib_ids, "B's activity must not appear in A's load report"


def test_trends_volume_scoped_to_own_activities(client, two_users, db):
    """GET /api/trends/volume must not fold another user's distance into the caller's window."""
    a, b = two_users
    # A logs a modest week; B logs a huge week in the same rolling window.
    _recent_activity(db, a.user.id, days_ago=1, distance_m=3000, name="A only")
    _recent_activity(db, b.user.id, days_ago=1, distance_m=80000, name="B only")

    _act_as(a.user)
    report = client.get("/api/trends/volume?range=7D").json()
    distance = next(
        m for m in report["rolling"]["metrics"] if m["metric"] == "distance_m"
    )
    assert distance["current_all"] == 3000, (
        "B's 80km must not inflate A's volume window"
    )


def test_refresh_scoped_to_callers_own_account(client, two_users, monkeypatch):
    """POST /api/activities/refresh must self-heal the caller's own account only (#502).

    Regression guard: before the fix the endpoint picked an arbitrary StravaAccount,
    so user B's refresh could drive a self-heal against user A's account.
    """
    a, b = two_users
    seen = []
    import app.jobs.self_heal as self_heal

    monkeypatch.setattr(
        self_heal, "maybe_enqueue_self_heal", lambda user_id: seen.append(user_id) or True
    )

    _act_as(b.user)
    resp = client.post("/api/activities/refresh")
    assert resp.status_code == 200
    assert resp.json()["status"] == "checking"
    assert seen == [b.user.id], "refresh must target B's own account, never A's"


def test_refresh_no_account_no_ops(client, db, monkeypatch):
    """A user with no linked Strava account gets a clean no-op, never another user's account."""
    # User A has a Strava account; user B (the caller) has none.
    owner = User(email="owner@test.dev")
    db.add(owner)
    db.commit()
    db.add(StravaAccount(
        user_id=owner.id, strava_athlete_id=999, access_token="t",
        refresh_token="r", expires_at=9999999999, scope="read",
    ))
    db.commit()
    caller = User(email="caller@test.dev")
    db.add(caller)
    db.commit()

    import app.jobs.self_heal as self_heal
    called = []
    monkeypatch.setattr(
        self_heal, "maybe_enqueue_self_heal", lambda user_id: called.append(user_id) or True
    )

    _act_as(caller)
    resp = client.post("/api/activities/refresh")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_account"
    assert called == [], "no self-heal may fire on another user's account"
