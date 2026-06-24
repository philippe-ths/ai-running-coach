"""P2.1 tenant-isolation negative contract (#119).

The new security contract for multi-user: a request authenticated as user A must
never read or mutate user B's data. These tests seed two independent users with a
full data graph each, authenticate as one, and assert that the other's resources
are DENIED (404) -- not served. The existing single-user suite is the positive
oracle (behaviour preserved for the owning user); this file is the negative one.

Auth is injected by overriding verify_clerk_session (the resolution itself is
covered by test_clerk_auth); here we pin the QUERY scoping that rides on top.
"""

from datetime import datetime, timedelta, timezone
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
