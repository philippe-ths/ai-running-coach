"""Every runner's history is re-analysed once when ANALYSIS_VERSION moves.

A change to what analysis stores (#1065) used to reach old activities only
through a manual per-runner trigger. The worker now starts one gentle, global
pass per analysis version at boot.
"""

from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.jobs import reanalyze_history as mod


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, key):
        self.store.pop(key, None)


class _DownRedis:
    def set(self, *a, **k):
        raise ConnectionError("redis down")


def _boot(redis, version=1, enqueue=None):
    enqueue = enqueue or MagicMock()
    with patch("app.core.queue.redis_conn", redis), patch.object(
        mod, "ANALYSIS_VERSION", version
    ), patch.object(mod.queue, "enqueue", enqueue):
        started = mod.reanalyze_if_analysis_changed()
    return started, enqueue


def test_a_new_version_queues_one_gentle_pass_over_every_runner():
    started, enqueue = _boot(_FakeRedis())

    assert started
    args = enqueue.call_args.args
    assert args == (mod.reanalyze_history_job, None, None, settings.AUTO_REANALYZE_BATCH_SIZE)
    assert settings.AUTO_REANALYZE_BATCH_SIZE < settings.REANALYZE_BATCH_SIZE


def test_a_restart_on_the_same_version_queues_nothing():
    redis = _FakeRedis()
    _boot(redis)

    started, enqueue = _boot(redis)

    assert not started
    enqueue.assert_not_called()


def test_a_version_bump_queues_a_fresh_pass():
    redis = _FakeRedis()
    _boot(redis, version=1)

    started, enqueue = _boot(redis, version=2)

    assert started
    enqueue.assert_called_once()


def test_a_redis_outage_never_stops_the_worker_booting():
    started, enqueue = _boot(_DownRedis())

    assert not started
    enqueue.assert_not_called()


def test_a_failed_enqueue_leaves_the_version_for_the_next_boot():
    redis = _FakeRedis()
    _boot(redis, enqueue=MagicMock(side_effect=ConnectionError("queue down")))

    started, enqueue = _boot(redis)

    assert started
    enqueue.assert_called_once()


def test_the_batch_size_rides_the_whole_chain():
    result = mod.ReanalyzeBatchResult(processed=[2, 1], next_cursor=1, remaining=5)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "reanalyze_history_batch", return_value=result
    ) as batch, patch.object(mod, "_schedule_next_batch") as sched:
        mod.reanalyze_history_job(None, None, 20)

    assert batch.call_args.kwargs["limit"] == 20
    sched.assert_called_once_with(1, None, 20)


def test_a_successor_queued_before_this_change_keeps_the_default_size():
    """Two-argument jobs already in the queue at deploy still run."""
    result = mod.ReanalyzeBatchResult(processed=[1], next_cursor=None, remaining=0)
    with patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
        mod, "reanalyze_history_batch", return_value=result
    ) as batch:
        mod.reanalyze_history_job(5, "some-user")

    assert batch.call_args.kwargs["limit"] == settings.REANALYZE_BATCH_SIZE


def test_the_worker_boot_calls_the_hook():
    from app import worker

    with patch("app.jobs.garmin_sync.start_chain_if_needed"), patch(
        "app.jobs.reanalyze_history.reanalyze_if_analysis_changed"
    ) as hook, patch.object(worker, "build_worker_runtime"), patch.object(
        worker, "run_worker_runtime"
    ), patch.object(worker, "init_sentry"), patch.object(worker, "init_logging"):
        worker.main()

    hook.assert_called_once()
