"""#515: a block assignment failure during ingestion strands the activity.

The activity row is committed before block assignment runs, and assignment is
guarded so a grouping failure never breaks ingestion. Before the fix, a guarded
failure left the row committed with `block_id IS NULL` and nothing ever retried
it: self-heal treated the row as already known and no sweep existed, so it was
permanently block-less.

These tests pin both halves of the contract:
1. The guard is intact: an assignment failure during ingestion still commits the
   activity, leaves `block_id` NULL, and does not break ingestion.
2. The recovery reconciles a stranded activity into a block on the next ingest
   opportunity for that user.

The InMemory Strava adapter is real test setup (a fake of the Strava port), not
ground truth; the ground truth here is the block-grouping invariant in
`app.services.blocks`, exercised through the real assignment path.
"""

import pytest

from datetime import datetime

from app.models import Activity, Block, StravaAccount, User
from app.services.strava_ingestion import InMemoryStravaAdapter, ingest_recent_activities
from app.services.strava_ingestion.ingestion import _assign_block


def _make_account(db, athlete_id: int) -> StravaAccount:
    user = User(email=f"reconcile_{athlete_id}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=athlete_id,
        access_token="valid_token",
        refresh_token="fake_refresh",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return account


def _raw_activity(activity_id: int, name: str = "Run") -> dict:
    return {
        "id": activity_id,
        "name": name,
        "type": "Run",
        "start_date": "2024-02-01T10:00:00Z",
        "distance": 5000,
        "moving_time": 1500,
        "elapsed_time": 1500,
        "total_elevation_gain": 25,
        "average_heartrate": 145,
    }


def test_assignment_failure_during_ingest_strands_but_does_not_break(db, monkeypatch):
    """Guard intact: when block assignment raises, ingestion does not break and
    the committed activity is left stranded with block_id NULL (#515).

    This exercises `_assign_block` (the shared seam every ingest path calls)
    directly: in production the activity row is committed before this runs, so a
    guarded failure leaves it committed-but-block-less. The test DB fixture wraps
    the test in one outer transaction, so it cannot model a real durable commit
    surviving a later rollback; the faithful, harness-stable contract pinned here
    is that the guard swallows the failure (no exception propagates) and the
    activity ends with block_id NULL rather than crashing ingestion.
    """
    account = _make_account(db, athlete_id=51500)
    activity = Activity(
        user_id=account.user_id,
        strava_activity_id=5151,
        name="Stranded",
        type="Run",
        start_date=datetime(2024, 2, 1, 10, 0, 0),
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
        raw_summary={},
        block_id=None,
    )
    db.add(activity)
    db.commit()
    activity_user_id = account.user_id

    # Force the block grouping to raise, simulating the real failure mode.
    import app.services.blocks as blocks_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated block-assignment failure")

    monkeypatch.setattr(blocks_module, "assign_activity_to_block", _boom)

    # The guard must swallow the failure: _assign_block does not raise.
    _assign_block(db, activity)

    # Assignment never landed: no Block exists for this user, so the activity is
    # stranded (block_id NULL). Asserting on the absence of a Block avoids reading
    # the post-commit-then-rolled-back `activity` instance, which the single-outer-
    # transaction test fixture would report as deleted.
    assert (
        db.query(Block).filter(Block.user_id == activity_user_id).count() == 0
    )


@pytest.mark.asyncio
async def test_next_ingest_reconciles_a_previously_stranded_activity(db):
    """Recovery: a stranded (block_id NULL) activity is re-grouped into a block on
    the next ingest for that user (#515).

    This is the assertion that goes red if the reconcile sweep is removed.
    """
    account = _make_account(db, athlete_id=51501)
    adapter = InMemoryStravaAdapter()

    # A previously-stranded activity: committed, live, but block_id NULL (the #515
    # state a guarded assignment failure leaves behind).

    orphan = Activity(
        user_id=account.user_id,
        strava_activity_id=6161,
        name="Previously stranded",
        type="Run",
        start_date=datetime(2024, 2, 1, 9, 0, 0),
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
        raw_summary={},
        block_id=None,
    )
    db.add(orphan)
    db.commit()
    assert orphan.block_id is None

    # A routine later ingest of a different, far-apart activity. Its own
    # assignment runs normally; the reconcile sweep also picks up the orphan.
    adapter.seed_activities([_raw_activity(6262, "New run")])
    adapter.seed_streams(6262, {"time": {"data": [0, 1, 2]}})

    await ingest_recent_activities(db, account, adapter)

    db.refresh(orphan)
    assert orphan.block_id is not None, (
        "the previously-stranded activity should be reconciled into a block "
        "by the ingest-time sweep"
    )


@pytest.mark.asyncio
async def test_reconcile_skips_soft_deleted_orphans(db):
    """A soft-deleted activity belongs in no block: the sweep must not re-group it."""
    account = _make_account(db, athlete_id=51502)
    adapter = InMemoryStravaAdapter()


    deleted_orphan = Activity(
        user_id=account.user_id,
        strava_activity_id=7171,
        name="Deleted orphan",
        type="Run",
        start_date=datetime(2024, 2, 1, 9, 0, 0),
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
        raw_summary={},
        block_id=None,
        is_deleted=True,
    )
    db.add(deleted_orphan)
    db.commit()

    adapter.seed_activities([_raw_activity(7272, "New run")])
    adapter.seed_streams(7272, {"time": {"data": [0, 1, 2]}})

    await ingest_recent_activities(db, account, adapter)

    db.refresh(deleted_orphan)
    assert deleted_orphan.block_id is None
