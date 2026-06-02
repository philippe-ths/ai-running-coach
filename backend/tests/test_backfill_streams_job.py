"""Backfill job: historical summary-only activities gain stream-derived analysis.

Covers eligibility selection, per-activity fetch+analyze+mark, batch bounding,
convergence on streamless activities, the no-notification guarantee, and the
self-pacing re-enqueue. See #110.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, ActivityStream, StravaAccount, User
from app.services.strava_ingestion import InMemoryStravaAdapter, set_strava_port


@pytest.fixture
def strava_adapter():
    adapter = InMemoryStravaAdapter()
    set_strava_port(adapter)
    yield adapter
    set_strava_port(None)


def _seed_user_and_account(db, athlete_id: int = 777):
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=athlete_id,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user, account


def _seed_summary_activity(db, user, strava_activity_id: int) -> Activity:
    """A historical activity imported summary-only: no streams, not yet backfilled."""
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        type="Run",
        name="Historical run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=30,
        avg_hr=145,
        average_speed_mps=3.3,
    )
    db.add(activity)
    db.commit()
    return activity


def _seed_streams(adapter, strava_activity_id: int) -> None:
    adapter.seed_streams(
        strava_activity_id,
        {
            "time": {"data": [0, 60, 120, 180]},
            "heartrate": {"data": [130, 140, 145, 148]},
            "distance": {"data": [0, 200, 400, 600]},
            "velocity_smooth": {"data": [3.0, 3.3, 3.4, 3.5]},
        },
    )


@pytest.mark.asyncio
async def test_batch_fetches_streams_and_marks_activity(db, strava_adapter):
    from app.jobs.backfill_streams import backfill_streams_batch

    user, _account = _seed_user_and_account(db)
    activity = _seed_summary_activity(db, user, 5001)
    _seed_streams(strava_adapter, 5001)

    result = await backfill_streams_batch(db, limit=settings.BACKFILL_BATCH_SIZE)

    # Streams were fetched and stored for the activity.
    stream_rows = (
        db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).count()
    )
    assert stream_rows > 0
    # The activity is marked attempted, so it will not be re-fetched.
    db.refresh(activity)
    assert activity.streams_backfilled_at is not None
    assert 5001 in result.processed
    assert result.remaining == 0


@pytest.mark.asyncio
async def test_eligibility_excludes_streamed_attempted_and_deleted(db):
    from app.jobs.backfill_streams import count_eligible

    user, _account = _seed_user_and_account(db)

    # Eligible: summary-only, no streams, never attempted.
    _seed_summary_activity(db, user, 6001)

    # Excluded: already has stream rows (normal pipeline fetched them).
    streamed = _seed_summary_activity(db, user, 6002)
    db.add(ActivityStream(activity_id=streamed.id, stream_type="time", data=[0, 1, 2]))
    db.commit()

    # Excluded: already attempted by an earlier backfill (marker set).
    attempted = _seed_summary_activity(db, user, 6003)
    attempted.streams_backfilled_at = datetime.now(timezone.utc)
    db.commit()

    # Excluded: soft-deleted.
    deleted = _seed_summary_activity(db, user, 6004)
    deleted.is_deleted = True
    db.commit()

    assert count_eligible(db) == 1


@pytest.mark.asyncio
async def test_batch_respects_limit_and_reports_remaining(db, strava_adapter):
    from app.jobs.backfill_streams import backfill_streams_batch

    user, _account = _seed_user_and_account(db)
    for sid in (7001, 7002, 7003):
        _seed_summary_activity(db, user, sid)
        _seed_streams(strava_adapter, sid)

    result = await backfill_streams_batch(db, limit=2)

    assert len(result.processed) == 2
    assert result.remaining == 1


@pytest.mark.asyncio
async def test_streamless_activity_is_marked_and_converges(db, strava_adapter):
    """An activity Strava has no streams for (manual entry, treadmill) must not
    be retried forever: it is marked attempted and drops out of eligibility."""
    from app.jobs.backfill_streams import backfill_streams_batch, count_eligible

    user, _account = _seed_user_and_account(db)
    activity = _seed_summary_activity(db, user, 8001)
    # No streams seeded in the adapter -> get_activity_streams returns None.

    result = await backfill_streams_batch(db, limit=settings.BACKFILL_BATCH_SIZE)

    db.refresh(activity)
    assert activity.streams_backfilled_at is not None
    assert 8001 in result.processed
    # No stream rows were created, yet nothing remains eligible: converged.
    assert (
        db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).count()
        == 0
    )
    assert count_eligible(db) == 0


@pytest.mark.asyncio
async def test_backfill_never_notifies(db, strava_adapter):
    """Backfilling old activities must not fire user-facing notifications."""
    from app.jobs.backfill_streams import backfill_streams_batch
    from app.services.notifications import InMemoryNotifier, set_notifier

    notifier = InMemoryNotifier()
    set_notifier(notifier)
    try:
        user, _account = _seed_user_and_account(db)
        activity = _seed_summary_activity(db, user, 9001)
        _seed_streams(strava_adapter, 9001)

        await backfill_streams_batch(db, limit=settings.BACKFILL_BATCH_SIZE)

        assert notifier.sent == []
        db.refresh(activity)
        assert activity.coach_notification_sent_at is None
    finally:
        set_notifier(None)


def test_job_schedules_next_batch_when_work_remains():
    from app.jobs import backfill_streams as mod

    result = mod.BackfillBatchResult(processed=[1, 2], remaining=5)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "backfill_streams_batch", new=AsyncMock(return_value=result)
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.backfill_streams_job()

    sched.assert_called_once()


def test_job_does_not_reschedule_when_drained():
    from app.jobs import backfill_streams as mod

    result = mod.BackfillBatchResult(processed=[1], remaining=0)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "backfill_streams_batch", new=AsyncMock(return_value=result)
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.backfill_streams_job()

    sched.assert_not_called()


def test_enqueue_backfill_counts_and_enqueues_first_batch(db):
    from app.jobs import backfill_streams as mod

    user, _account = _seed_user_and_account(db)
    _seed_summary_activity(db, user, 4001)

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        eligible = mod.enqueue_backfill(db)

    assert eligible == 1
    fake_queue.enqueue.assert_called_once()
    _args, kwargs = fake_queue.enqueue.call_args
    assert kwargs["job_id"] == "backfill_streams"


def test_enqueue_backfill_is_noop_when_nothing_eligible(db):
    from app.jobs import backfill_streams as mod

    _seed_user_and_account(db)

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        eligible = mod.enqueue_backfill(db)

    assert eligible == 0
    fake_queue.enqueue.assert_not_called()


def test_endpoint_triggers_backfill_and_reports_eligible(client, db):
    from app.jobs import backfill_streams as mod

    user, _account = _seed_user_and_account(db)
    _seed_summary_activity(db, user, 3001)
    _seed_summary_activity(db, user, 3002)

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        resp = client.post("/api/activities/backfill-streams")

    assert resp.status_code == 200
    assert resp.json()["eligible"] == 2
    fake_queue.enqueue.assert_called_once()
