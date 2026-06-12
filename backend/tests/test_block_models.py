"""A1 data model: blocks, exchanges, coaching_relationship (issue #235, ADR 0011).

Fixtures here are synthetic test setup (exercising model shape and constraints),
not ground truth about real training data.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Activity, Block, CoachingRelationship, Exchange, User


def _make_user(db) -> User:
    user = User(email=f"block-test-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.commit()
    return user


def _make_activity(db, user, *, start=None, **overrides) -> Activity:
    start = start or datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    fields = dict(
        user_id=user.id,
        strava_activity_id=overrides.pop(
            "strava_activity_id", int(uuid.uuid4().int % 10**12)
        ),
        start_date=start,
        type="Run",
        name="Morning Run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10.0,
        raw_summary={},
    )
    fields.update(overrides)
    activity = Activity(**fields)
    db.add(activity)
    db.commit()
    return activity


def test_block_groups_activities_via_block_id(db):
    user = _make_user(db)
    a1 = _make_activity(db, user)
    a2 = _make_activity(
        db, user, start=a1.start_date + timedelta(seconds=a1.elapsed_time_s + 600)
    )

    block = Block(
        user_id=user.id,
        start_date=a1.start_date,
        end_date=a2.start_date + timedelta(seconds=a2.elapsed_time_s),
        primary_activity_id=a1.id,
    )
    db.add(block)
    db.commit()

    a1.block_id = block.id
    a2.block_id = block.id
    db.commit()

    assert block.id is not None
    assert block.user_corrected is False
    assert {a.id for a in block.activities} == {a1.id, a2.id}


def _make_block(db, user, activity) -> Block:
    block = Block(
        user_id=user.id,
        start_date=activity.start_date,
        end_date=activity.start_date + timedelta(seconds=activity.elapsed_time_s),
        primary_activity_id=activity.id,
    )
    db.add(block)
    db.commit()
    return block


def test_exchange_is_unique_per_block(db):
    user = _make_user(db)
    activity = _make_activity(db, user)
    block = _make_block(db, user, activity)

    exchange = Exchange(user_id=user.id, block_id=block.id)
    db.add(exchange)
    db.commit()

    assert exchange.id is not None
    assert exchange.opener_sent_at is None
    assert exchange.fuller_sent_at is None

    db.add(Exchange(user_id=user.id, block_id=block.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_coaching_relationship_is_singleton_per_user(db):
    user = _make_user(db)

    db.add(CoachingRelationship(user_id=user.id))
    db.commit()

    db.add(CoachingRelationship(user_id=user.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_first_profile_read_creates_coaching_relationship(client, db):
    response = client.get("/api/profile")
    assert response.status_code == 200

    rows = db.query(CoachingRelationship).all()
    assert len(rows) == 1

    # A second read does not create a second row.
    client.get("/api/profile")
    assert db.query(CoachingRelationship).count() == 1
