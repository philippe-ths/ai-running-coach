"""A1 split/merge correction API (ADR 0011): API-level only (no UI until I3).

Corrections set user_corrected, reassign members and recompute the block, and
NEVER send anything (AC4) — there is no notifier in this surface at all.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models import Activity, Block, Exchange, User
from app.services.blocks import assign_activity_to_block

T0 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
GAP = 1800


def _user(db):
    user = User(email=f"api-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _activity(db, user, *, start, elapsed=1500, type="Run"):
    a = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                 start_date=start, type=type, name=type, distance_m=5000,
                 moving_time_s=elapsed, elapsed_time_s=elapsed, elev_gain_m=0.0,
                 raw_summary={})
    db.add(a)
    db.commit()
    return a


def test_split_endpoint_corrects_grouping(db, client):
    user = _user(db)
    walk = _activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run = _activity(db, user, start=T0 + timedelta(seconds=1800), elapsed=2400)
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.commit()

    resp = client.post(f"/api/blocks/{block.id}/split", json={"activity_id": str(run.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(b["user_corrected"] for b in body)
    db.refresh(walk)
    db.refresh(run)
    assert walk.block_id != run.block_id
    assert db.query(Exchange).count() == 2  # each block keeps one exchange


def test_split_rejects_non_member_and_solo(db, client):
    user = _user(db)
    solo = _activity(db, user, start=T0)
    block = assign_activity_to_block(db, solo, gap_seconds=GAP)
    other = _activity(db, user, start=T0 + timedelta(days=1))
    assign_activity_to_block(db, other, gap_seconds=GAP)
    db.commit()

    # not a member of this block
    resp = client.post(f"/api/blocks/{block.id}/split", json={"activity_id": str(other.id)})
    assert resp.status_code == 422

    # splitting a block-of-one would leave an empty half
    resp = client.post(f"/api/blocks/{block.id}/split", json={"activity_id": str(solo.id)})
    assert resp.status_code == 422

    resp = client.post(f"/api/blocks/{uuid4()}/split", json={"activity_id": str(solo.id)})
    assert resp.status_code == 404


def test_merge_endpoint_combines_adjacent_blocks(db, client):
    user = _user(db)
    first = _activity(db, user, start=T0, elapsed=1200, type="Walk")
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)
    second = _activity(db, user, start=T0 + timedelta(seconds=1200 + GAP * 2), elapsed=2400)
    block2 = assign_activity_to_block(db, second, gap_seconds=GAP)
    assert block1.id != block2.id
    db.commit()

    resp = client.post(f"/api/blocks/{block1.id}/merge", json={"other_block_id": str(block2.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_corrected"] is True
    assert str(second.id) == body["primary_activity_id"]  # the run wins
    db.refresh(first)
    db.refresh(second)
    assert first.block_id == second.block_id
    assert db.query(Block).count() == 1
    assert db.query(Exchange).count() == 1


def test_merge_rejects_non_adjacent_blocks(db, client):
    user = _user(db)
    a = _activity(db, user, start=T0)
    block_a = assign_activity_to_block(db, a, gap_seconds=GAP)
    b = _activity(db, user, start=T0 + timedelta(days=1))
    assign_activity_to_block(db, b, gap_seconds=GAP)
    c = _activity(db, user, start=T0 + timedelta(days=2))
    block_c = assign_activity_to_block(db, c, gap_seconds=GAP)
    db.commit()

    # another block sits between them: not adjacent
    resp = client.post(f"/api/blocks/{block_a.id}/merge", json={"other_block_id": str(block_c.id)})
    assert resp.status_code == 422

    resp = client.post(f"/api/blocks/{block_a.id}/merge", json={"other_block_id": str(uuid4())})
    assert resp.status_code == 404
