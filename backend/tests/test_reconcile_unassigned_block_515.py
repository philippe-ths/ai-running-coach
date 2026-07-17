"""#515: a block assignment failure during ingestion strands the activity.

The activity row is committed before block assignment runs, and assignment is
guarded so a grouping failure never breaks ingestion. Before the fix, a guarded
failure left the row committed with `block_id IS NULL` and nothing ever retried
it: self-heal treated the row as already known and no sweep existed, so it was
permanently block-less.

These tests pin the contract:
1. The guard is intact: an assignment failure still leaves the activity stranded
   (block_id NULL) and does not break ingestion.
2. The recovery reconciles a stranded activity into a block on the next ingest
   for that user.
3. The recovery sweep runs at most ONCE per batch, not once per activity, so an
   N-activity sync does not multiply the sweep.
4. The sweep is bounded: a backlog (or a chronically-unassignable orphan) is
   capped per run so cost and logs cannot blow up.

The InMemory Strava adapter is real test setup (a fake of the Strava port), not
ground truth; the ground truth here is the block-grouping invariant in
`app.services.blocks`, exercised through the real assignment path.
"""

import pytest

from datetime import datetime, timedelta

from app.models import Activity, Block, StravaAccount, User
from app.services.strava_ingestion import InMemoryStravaAdapter, ingest_recent_activities
from app.services.strava_ingestion import ingestion as ingestion_module
from app.services.blocks import (
    RECONCILE_MAX_PER_RUN,
    assign_block_guarded,
    reconcile_unassigned_activities,
)


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


def _make_orphan(db, user_id, *, strava_id: int, start: datetime, **overrides) -> Activity:
    """A previously-stranded activity: committed, live, but block_id NULL (the
    #515 state a guarded assignment failure leaves behind)."""
    fields = dict(
        user_id=user_id,
        strava_activity_id=strava_id,
        name=f"Stranded {strava_id}",
        type="Run",
        start_date=start,
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10,
        raw_summary={},
        block_id=None,
    )
    fields.update(overrides)
    orphan = Activity(**fields)
    db.add(orphan)
    db.commit()
    return orphan


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

    # The guard must swallow the failure: assign_block_guarded does not raise.
    assign_block_guarded(db, activity)

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


@pytest.mark.asyncio
async def test_sweep_runs_once_per_batch_not_once_per_activity(db, monkeypatch):
    """The recovery sweep is invoked at most once for a multi-activity batch.

    Regression guard for the first review finding: the sweep used to run inside
    `_assign_block`, which the ingest loop calls per activity, so an N-activity
    sync ran the full stranded-sweep N times. It must run once per batch.
    """
    account = _make_account(db, athlete_id=51503)
    adapter = InMemoryStravaAdapter()

    raws = [_raw_activity(8000 + i, f"Run {i}") for i in range(4)]
    for raw in raws:
        adapter.seed_activities([raw])
        adapter.seed_streams(raw["id"], {"time": {"data": [0, 1, 2]}})
    # seed_activities replaces; seed the full set once.
    adapter.seed_activities(raws)

    calls = {"n": 0}
    real = reconcile_unassigned_activities

    def _spy(db_, user_id, **kwargs):
        calls["n"] += 1
        return real(db_, user_id, **kwargs)

    monkeypatch.setattr(ingestion_module, "reconcile_unassigned_activities", _spy)

    ingested, _ = await ingest_recent_activities(db, account, adapter)

    assert len(ingested) == 4
    # Once for the whole batch, not once per activity (would be 4).
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_sweep_is_bounded_per_run(db):
    """A backlog larger than the cap reconciles at most RECONCILE_MAX_PER_RUN
    activities in one sweep, so cost and logs cannot blow up (#515 review)."""
    account = _make_account(db, athlete_id=51504)

    backlog = RECONCILE_MAX_PER_RUN + 5
    base = datetime(2024, 1, 1, 6, 0, 0)
    for i in range(backlog):
        # Spread far apart so each forms its own block-of-one (no joining).
        _make_orphan(db, account.user_id, strava_id=9000 + i, start=base + timedelta(days=i))

    reconciled = reconcile_unassigned_activities(db, account.user_id)

    assert reconciled == RECONCILE_MAX_PER_RUN

    remaining = (
        db.query(Activity)
        .filter(Activity.user_id == account.user_id, Activity.block_id.is_(None))
        .count()
    )
    assert remaining == backlog - RECONCILE_MAX_PER_RUN


@pytest.mark.asyncio
async def test_chronically_unassignable_orphan_is_logged_quietly_not_with_stacktrace(
    db, monkeypatch, caplog
):
    """A permanently-failing orphan must not emit a full stack trace every run.

    Regression guard for the second review finding (log-noise landmine): the
    per-orphan failure path logs a single concise WARNING, not logger.exception.
    """
    import logging

    account = _make_account(db, athlete_id=51505)
    _make_orphan(db, account.user_id, strava_id=9999, start=datetime(2024, 2, 1, 9, 0, 0))

    import app.services.blocks as blocks_module

    def _boom(*args, **kwargs):
        raise RuntimeError("permanently unassignable")

    monkeypatch.setattr(blocks_module, "assign_activity_to_block", _boom)

    with caplog.at_level(logging.WARNING):
        reconciled = reconcile_unassigned_activities(db, account.user_id)

    assert reconciled == 0
    reconcile_records = [
        r for r in caplog.records if "block reconcile failed" in r.getMessage()
    ]
    assert len(reconcile_records) == 1
    # A concise warning, not a full-stack-trace ERROR.
    assert reconcile_records[0].levelno == logging.WARNING
    assert reconcile_records[0].exc_info is None
