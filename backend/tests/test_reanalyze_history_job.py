"""Bulk re-analysis job: already-analyzed history re-derives from stored streams.

Covers eligibility selection (has-streams, not-deleted), per-activity re-analysis
from stored streams (no Strava calls), cursor pagination across batch boundaries
without gaps or repeats, convergence, the no-notification guarantee, and the
self-pacing re-enqueue. See #300.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.core.config import settings
from app.models import Activity, ActivityStream, DerivedMetric, StravaAccount, User


def _seed_user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    account = StravaAccount(
        user_id=user.id,
        strava_athlete_id=777,
        access_token="t",
        refresh_token="r",
        expires_at=9999999999,
        scope="read,activity:read_all",
    )
    db.add(account)
    db.commit()
    return user


def _seed_activity(db, user, strava_activity_id: int, *, with_streams: bool = True) -> Activity:
    """A historical, already-analyzed activity with stored streams."""
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
    if with_streams:
        samples = list(range(0, 720, 30))  # 24 samples, 12 min
        streams = {
            "time": samples,
            "heartrate": [130 + (i % 20) for i in range(len(samples))],
            "distance": [i * 100 for i in range(len(samples))],
            "velocity_smooth": [3.3 for _ in samples],
        }
        for stream_type, data in streams.items():
            db.add(ActivityStream(activity_id=activity.id, stream_type=stream_type, data=data))
        db.commit()
    return activity


def _has_metric(db, activity: Activity) -> bool:
    return (
        db.query(DerivedMetric).filter(DerivedMetric.activity_id == activity.id).count() > 0
    )


def test_batch_reanalyzes_streamed_activity(db):
    from app.jobs.reanalyze_history import reanalyze_history_batch

    user = _seed_user(db)
    activity = _seed_activity(db, user, 5001)

    result = reanalyze_history_batch(db, limit=settings.REANALYZE_BATCH_SIZE)

    # Re-analysis produced a DerivedMetric from the stored streams (no Strava call).
    assert _has_metric(db, activity)
    assert 5001 in result.processed
    assert result.next_cursor is None
    assert result.remaining == 0


def test_eligibility_excludes_streamless_and_deleted(db):
    from app.jobs.reanalyze_history import count_eligible

    user = _seed_user(db)

    # Eligible: has stored streams, not deleted.
    _seed_activity(db, user, 6001, with_streams=True)

    # Excluded: no stored streams (summary-only import — nothing to re-derive).
    _seed_activity(db, user, 6002, with_streams=False)

    # Excluded: soft-deleted, even with streams.
    deleted = _seed_activity(db, user, 6003, with_streams=True)
    deleted.is_deleted = True
    db.commit()

    assert count_eligible(db) == 1


def test_batch_paginates_by_cursor_without_gaps_or_repeats(db):
    from app.jobs.reanalyze_history import reanalyze_history_batch

    user = _seed_user(db)
    for sid in (9101, 9102, 9103):
        _seed_activity(db, user, sid)

    # First batch (newest first): processes the two highest ids, advances cursor.
    batch1 = reanalyze_history_batch(db, limit=2)
    assert batch1.processed == [9103, 9102]
    assert batch1.next_cursor == 9102
    assert batch1.remaining == 1

    # Second batch continues strictly past the cursor, drains the set.
    batch2 = reanalyze_history_batch(db, limit=2, cursor=batch1.next_cursor)
    assert batch2.processed == [9101]
    assert batch2.next_cursor is None
    assert batch2.remaining == 0

    # The chain covered every activity exactly once, no gaps or repeats.
    seen = batch1.processed + batch2.processed
    assert sorted(seen) == [9101, 9102, 9103]
    assert len(seen) == len(set(seen))


def test_short_batch_does_not_advance_cursor(db):
    """A batch smaller than the limit means the set is drained, so no successor
    is scheduled even though a cursor-bearing batch ran."""
    from app.jobs.reanalyze_history import reanalyze_history_batch

    user = _seed_user(db)
    _seed_activity(db, user, 8001)

    result = reanalyze_history_batch(db, limit=100)

    assert result.processed == [8001]
    assert result.next_cursor is None


def test_failure_on_one_activity_does_not_halt_the_batch(db):
    """A re-analysis error is logged and skipped; the rest of the batch proceeds
    and the activity is still reported processed (convergence over completeness)."""
    from app.jobs import reanalyze_history as mod

    user = _seed_user(db)
    _seed_activity(db, user, 7001)
    _seed_activity(db, user, 7002)

    calls = {"n": 0}

    def flaky_analyze(db_, activity_id, skip_baseline=False):
        calls["n"] += 1
        if calls["n"] == 1:  # newest (7002) blows up
            raise RuntimeError("boom")
        return MagicMock()

    with patch.object(mod, "analyze", side_effect=flaky_analyze):
        result = mod.reanalyze_history_batch(db, limit=100)

    # Both activities were attempted and reported despite the first failing.
    assert calls["n"] == 2
    assert sorted(result.processed) == [7001, 7002]


def test_baseline_recomputed_once_per_user_after_the_batch(db):
    """#366: the runner baseline is recomputed once per touched user after the
    batch, not once per activity. analyze() is invoked with skip_baseline=True,
    so the only recompute is the job's single end-of-batch pass."""
    from app.jobs import reanalyze_history as mod

    user = _seed_user(db)
    _seed_activity(db, user, 8001)
    _seed_activity(db, user, 8002)
    _seed_activity(db, user, 8003)

    seen_skip = []

    def tracking_analyze(db_, activity_id, skip_baseline=False):
        seen_skip.append(skip_baseline)
        return MagicMock()

    with patch.object(mod, "analyze", side_effect=tracking_analyze), patch.object(
        mod, "recompute_runner_baseline", wraps=mod.recompute_runner_baseline
    ) as spy:
        result = mod.reanalyze_history_batch(db, limit=100)

    assert sorted(result.processed) == [8001, 8002, 8003]
    # analyze deferred the baseline on every activity...
    assert seen_skip == [True, True, True]
    # ...and the job recomputed it exactly once, for that one user.
    assert spy.call_count == 1
    assert spy.call_args.args[1] == user.id


def test_reanalysis_never_notifies(db):
    from app.jobs.reanalyze_history import reanalyze_history_batch
    from app.services.notifications import InMemoryNotifier, set_notifier

    notifier = InMemoryNotifier()
    set_notifier(notifier)
    try:
        user = _seed_user(db)
        activity = _seed_activity(db, user, 9001)

        reanalyze_history_batch(db, limit=settings.REANALYZE_BATCH_SIZE)

        assert notifier.sent == []
        db.refresh(activity)
        assert activity.coach_notification_sent_at is None
    finally:
        set_notifier(None)


def test_job_schedules_next_batch_when_cursor_remains():
    from app.jobs import reanalyze_history as mod

    result = mod.ReanalyzeBatchResult(processed=[2, 1], next_cursor=1, remaining=5)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "reanalyze_history_batch", return_value=result
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.reanalyze_history_job()

    sched.assert_called_once_with(1)


def test_job_does_not_reschedule_when_drained():
    from app.jobs import reanalyze_history as mod

    result = mod.ReanalyzeBatchResult(processed=[1], next_cursor=None, remaining=0)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "reanalyze_history_batch", return_value=result
    ), patch.object(mod, "_schedule_next_batch") as sched:
        mod.reanalyze_history_job()

    sched.assert_not_called()


def test_enqueue_reanalyze_counts_and_enqueues_first_batch(db):
    from app.jobs import reanalyze_history as mod

    user = _seed_user(db)
    _seed_activity(db, user, 4001)

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        eligible = mod.enqueue_reanalyze(db)

    assert eligible == 1
    fake_queue.enqueue.assert_called_once()
    _args, kwargs = fake_queue.enqueue.call_args
    assert kwargs["job_id"] == "reanalyze_history"


def test_enqueue_reanalyze_is_noop_when_nothing_eligible(db):
    from app.jobs import reanalyze_history as mod

    _seed_user(db)  # no activities with streams

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        eligible = mod.enqueue_reanalyze(db)

    assert eligible == 0
    fake_queue.enqueue.assert_not_called()


def test_endpoint_triggers_reanalyze_and_reports_eligible(client, db):
    from app.jobs import reanalyze_history as mod

    user = _seed_user(db)
    _seed_activity(db, user, 3001)
    _seed_activity(db, user, 3002)

    fake_queue = MagicMock()
    with patch.object(mod, "queue", fake_queue):
        resp = client.post("/api/activities/reanalyze")

    assert resp.status_code == 200
    assert resp.json()["eligible"] == 2
    assert resp.json()["status"] == "scheduled"
    fake_queue.enqueue.assert_called_once()
