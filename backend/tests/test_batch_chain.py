"""Shared self-pacing batch-chain mechanics (#698).

Pins the harness both maintenance jobs (backfill #110, reanalyze #300) and the
self-heal trigger (#123) now share: the eligible-set query fragments and count,
the per-user enqueue-cooldown idiom (incl. its degrade-open/closed fallback), and
the deferred successor scheduling. RQ scheduling itself is exercised via a fake
queue (the real enqueue_in needs a live Redis/worker), so these assert the harness
calls the queue correctly, not that RQ actually fires the job.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy import select

from app.jobs import batch_chain
from app.models import Activity, ActivityStream, User


def _seed_user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _seed_activity(db, user, strava_activity_id, *, with_streams: bool) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_activity_id,
        start_date=datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
        type="Run",
        name="run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10.0,
        avg_hr=140,
    )
    db.add(activity)
    db.commit()
    if with_streams:
        db.add(ActivityStream(activity_id=activity.id, stream_type="time", data=[0, 1, 2]))
        db.commit()
    return activity


# --- eligible-set query fragments ----------------------------------------------

def test_has_streams_exists_selects_only_stream_bearing_rows(db):
    user = _seed_user(db)
    streamed = _seed_activity(db, user, 101, with_streams=True)
    streamless = _seed_activity(db, user, 102, with_streams=False)

    with_streams = select(Activity).where(batch_chain.has_streams_exists())
    without_streams = select(Activity).where(~batch_chain.has_streams_exists())

    assert [a.id for a in db.execute(with_streams).scalars()] == [streamed.id]
    assert [a.id for a in db.execute(without_streams).scalars()] == [streamless.id]


def test_scope_to_user_filters_and_coerces_string_id(db):
    user_a = _seed_user(db)
    user_b = _seed_user(db)
    a = _seed_activity(db, user_a, 201, with_streams=True)
    _seed_activity(db, user_b, 202, with_streams=True)

    base = select(Activity)
    # user_id rides through RQ as a string; the helper must coerce it.
    scoped = batch_chain.scope_to_user(base, str(user_a.id))
    assert [row.id for row in db.execute(scoped).scalars()] == [a.id]


def test_scope_to_user_none_is_a_global_pass(db):
    user = _seed_user(db)
    _seed_activity(db, user, 301, with_streams=True)
    _seed_activity(db, user, 302, with_streams=False)

    assert batch_chain.scope_to_user(select(Activity), None) is not None
    all_rows = db.execute(batch_chain.scope_to_user(select(Activity), None)).scalars().all()
    assert len(all_rows) == 2


def test_count_eligible_counts_statement_rows(db):
    user = _seed_user(db)
    _seed_activity(db, user, 401, with_streams=True)
    _seed_activity(db, user, 402, with_streams=True)
    _seed_activity(db, user, 403, with_streams=False)

    stmt = select(Activity).where(batch_chain.has_streams_exists())
    assert batch_chain.count_eligible(db, stmt) == 2


# --- the per-user enqueue-cooldown idiom ---------------------------------------

def test_acquire_enqueue_slot_returns_true_when_slot_acquired():
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    with patch("app.core.queue.redis_conn", fake_redis):
        assert batch_chain.acquire_enqueue_slot("k", 120) is True
    # Atomic SET NX EX with the cooldown TTL.
    assert fake_redis.set.call_args.kwargs == {"nx": True, "ex": 120}


def test_acquire_enqueue_slot_returns_false_when_throttled():
    fake_redis = MagicMock()
    fake_redis.set.return_value = None  # key already present within the window
    with patch("app.core.queue.redis_conn", fake_redis):
        assert batch_chain.acquire_enqueue_slot("k", 120) is False


def test_acquire_enqueue_slot_degrades_open_on_redis_error():
    """A Redis outage must not block a legitimate maintenance run: degrade OPEN."""
    fake_redis = MagicMock()
    fake_redis.set.side_effect = RuntimeError("redis down")
    with patch("app.core.queue.redis_conn", fake_redis):
        assert batch_chain.acquire_enqueue_slot("k", 120, degrade_open=True) is True


def test_acquire_enqueue_slot_degrades_closed_on_redis_error():
    """A best-effort safety net (self-heal) skips on a Redis outage: degrade CLOSED."""
    fake_redis = MagicMock()
    fake_redis.set.side_effect = RuntimeError("redis down")
    with patch("app.core.queue.redis_conn", fake_redis):
        assert batch_chain.acquire_enqueue_slot("k", 120, degrade_open=False) is False


# --- deferred successor scheduling ---------------------------------------------

def test_schedule_after_enqueues_deferred_with_args():
    def _job(cursor, user_id):  # a stand-in chain entrypoint
        return None

    fake_queue = MagicMock()
    with patch("app.core.queue.queue", fake_queue):
        batch_chain.schedule_after(_job, 300, 42, "user-1")

    fake_queue.enqueue_in.assert_called_once()
    args = fake_queue.enqueue_in.call_args.args
    # (timedelta(seconds=pause), job_func, *chain_args)
    assert args[0].total_seconds() == 300
    assert args[1] is _job
    assert args[2:] == (42, "user-1")
