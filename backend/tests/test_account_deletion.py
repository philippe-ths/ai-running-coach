"""Account deletion with full cascade (P2.3, #121).

Oracle = the schema's FK graph: after deleting a user, NO row in any table still
references them, and another user's data is untouched. The endpoint removes the
Clerk user first (ADR 0022) and aborts the local cascade if that fails, so the
operation is all-or-nothing.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import (
    Activity,
    ActivityStream,
    Block,
    CheckIn,
    CoachChatMessage,
    CoachNarrative,
    CoachReport,
    CoachingContext,
    CoachingRelationship,
    DerivedMetric,
    Exchange,
    RunnerBaseline,
    StravaAccount,
    StravaImport,
    User,
    UserMaterial,
    UserProfile,
)
from app.services.account_deletion import delete_user_account


def _seed_full_user(db, *, email, athlete_id, strava_activity_id, hash_suffix):
    """One row in every user-owned table, so the cascade is proven per table."""
    user = User(email=email)
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=athlete_id, access_token="t",
        refresh_token="r", expires_at=1999999999, scope="read",  # 32-bit-safe
    ))
    db.add(StravaImport(user_id=user.id, since_date=date(2026, 1, 1)))
    db.add(CoachingRelationship(user_id=user.id))
    db.add(CoachingContext(user_id=user.id, kind="hr_confound", key="heat"))
    db.add(CoachNarrative(user_id=user.id, narrative="the story so far"))
    db.add(RunnerBaseline(user_id=user.id, typical_easy_hr=145.0))
    db.add(UserMaterial(
        user_id=user.id, kind="other", title="mine", filename="m.md",
        raw_text="private", content_hash=f"hash-{hash_suffix}", status="active",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="run",
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
    db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=[140, 141]))
    db.add(CheckIn(activity_id=activity.id, rpe=5))
    db.add(CoachReport(
        activity_id=activity.id, report={}, meta={}, context_pack={},
        prompt_id="x", schema_version="2.0", is_fallback=False,
    ))
    db.add(CoachChatMessage(activity_id=activity.id, role="user", content="hi"))
    block = Block(
        user_id=user.id, start_date=activity.start_date,
        end_date=activity.start_date + timedelta(seconds=1500),
        primary_activity_id=activity.id,
    )
    db.add(block)
    db.commit()
    # An activity belongs to the block, exercising the activities<->blocks cycle.
    activity.block_id = block.id
    db.add(Exchange(user_id=user.id, block_id=block.id, opened_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(user)
    db.refresh(activity)
    # Capture raw ids/email up front: after deletion the ORM instances are gone,
    # and attribute access on them would trigger a refresh (ObjectDeletedError).
    return SimpleNamespace(
        user=user, activity=activity, block=block,
        user_id=user.id, activity_id=activity.id, email=user.email,
    )


# Every (model, owner-column) pair that must be empty for a deleted user.
_USER_SCOPED = [
    (UserProfile, "user_id"), (StravaAccount, "user_id"), (StravaImport, "user_id"),
    (CoachingRelationship, "user_id"), (CoachingContext, "user_id"),
    (CoachNarrative, "user_id"), (RunnerBaseline, "user_id"),
    (UserMaterial, "user_id"), (Activity, "user_id"), (Block, "user_id"),
    (Exchange, "user_id"),
]
_ACTIVITY_SCOPED = [DerivedMetric, ActivityStream, CheckIn, CoachReport, CoachChatMessage]


def _residual_rows(db, ctx) -> int:
    """Count every row still owned by ctx across all tables (uses raw ids so it
    works after the ORM instances are deleted)."""
    total = 0
    for model, col in _USER_SCOPED:
        total += db.query(model).filter(getattr(model, col) == ctx.user_id).count()
    for model in _ACTIVITY_SCOPED:
        total += db.query(model).filter(model.activity_id == ctx.activity_id).count()
    total += db.query(User).filter(User.id == ctx.user_id).count()
    return total


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


def test_delete_user_account_removes_every_owned_row(db):
    a = _seed_full_user(db, email="a@del.dev", athlete_id=1, strava_activity_id=11, hash_suffix="a")
    b = _seed_full_user(db, email="b@del.dev", athlete_id=2, strava_activity_id=22, hash_suffix="b")
    assert _residual_rows(db, a) > 10  # the full graph is present

    delete_user_account(db, a.user_id)

    assert _residual_rows(db, a) == 0  # nothing of A's survives, in any table
    assert _residual_rows(db, b) > 10  # B is completely untouched


def test_endpoint_deletes_account_and_calls_clerk(client, db):
    a = _seed_full_user(db, email="a@del.dev", athlete_id=1, strava_activity_id=11, hash_suffix="a")
    b = _seed_full_user(db, email="b@del.dev", athlete_id=2, strava_activity_id=22, hash_suffix="b")
    _act_as(a.user)
    with patch("app.api.account.delete_clerk_user_by_email", return_value=True) as clerk:
        resp = client.delete("/api/account")
    assert resp.status_code == 204
    clerk.assert_called_once_with(a.email)
    assert _residual_rows(db, a) == 0
    assert _residual_rows(db, b) > 10


def test_endpoint_aborts_local_cascade_when_clerk_delete_fails(client, db):
    a = _seed_full_user(db, email="a@del.dev", athlete_id=1, strava_activity_id=11, hash_suffix="a")
    _act_as(a.user)
    before = _residual_rows(db, a)
    with patch("app.api.account.delete_clerk_user_by_email", return_value=False):
        resp = client.delete("/api/account")
    assert resp.status_code == 502
    # Nothing was deleted locally — the operation is all-or-nothing and retryable.
    assert _residual_rows(db, a) == before
