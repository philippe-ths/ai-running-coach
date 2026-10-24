"""Every runner's history is re-analysed once when ANALYSIS_VERSION moves.

A change to what analysis stores (#1065) used to reach old activities only
through a manual per-runner trigger. The worker now starts one gentle, global
pass per analysis version at boot.
"""

from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.jobs import reanalyze_history as mod


class _FakeRedis:
    """Values come back as bytes, as the real client returns them."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        value = self.store.get(key)
        return None if value is None else str(value).encode()

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, key):
        self.store.pop(key, None)


class _DownRedis:
    def get(self, *a, **k):
        raise ConnectionError("redis down")

    def set(self, *a, **k):
        raise ConnectionError("redis down")


class _FakeQueue:
    """Records what is enqueued, now or deferred, by job id."""

    def __init__(self, enqueue=None):
        self.waiting = []  # (job_id, args)
        self.scheduled_job_registry = MagicMock()
        self.scheduled_job_registry.get_job_ids.side_effect = lambda: [
            job_id for job_id, _ in self.waiting
        ]
        self.enqueue = enqueue or MagicMock(side_effect=self._enqueue)

    def _enqueue(self, func, *args, job_id=None, **_):
        self.waiting.append((job_id, args))

    def enqueue_in(self, _delay, func, *args, job_id=None, **_):
        self.waiting.append((job_id, args))

    def get_job_ids(self):
        return []


def _boot(redis, version=1, enqueue=None, fake_queue=None):
    fake_queue = fake_queue or _FakeQueue(enqueue)
    with patch("app.core.queue.redis_conn", redis), patch.object(
        mod, "ANALYSIS_VERSION", version
    ), patch.object(mod, "queue", fake_queue):
        started = mod.reanalyze_if_analysis_changed()
    return started, fake_queue.enqueue


def test_a_new_version_queues_one_gentle_pass_over_every_runner():
    started, enqueue = _boot(_FakeRedis())

    assert started
    args = enqueue.call_args.args
    assert args == (
        mod.reanalyze_history_job, None, None, settings.AUTO_REANALYZE_BATCH_SIZE, 1
    )
    assert settings.AUTO_REANALYZE_BATCH_SIZE < settings.REANALYZE_BATCH_SIZE


def test_a_restart_while_the_pass_is_waiting_queues_nothing():
    redis = _FakeRedis()
    fake_queue = _FakeQueue()
    _boot(redis, fake_queue=fake_queue)

    started, _ = _boot(redis, fake_queue=fake_queue)

    assert not started
    assert len(fake_queue.waiting) == 1


def test_a_restart_after_the_pass_finished_queues_nothing():
    redis = _FakeRedis()
    redis.store[mod._APPLIED_KEY.format(1)] = "done"

    started, enqueue = _boot(redis)

    assert not started
    enqueue.assert_not_called()


def test_an_interrupted_pass_resumes_from_its_last_finished_batch():
    """#1122: a redeploy or a failed batch ended the chain with nothing waiting."""
    redis = _FakeRedis()
    redis.store[mod._APPLIED_KEY.format(1)] = "at:4242"

    started, enqueue = _boot(redis)

    assert started
    assert enqueue.call_args.args[1:] == (4242, None, settings.AUTO_REANALYZE_BATCH_SIZE, 1)


def test_a_marker_from_before_progress_was_recorded_reruns_the_pass():
    """The old marker was set when the pass was queued, so it says nothing about
    whether the pass finished; re-analysis is idempotent, so run it again."""
    redis = _FakeRedis()
    redis.store[mod._APPLIED_KEY.format(1)] = "1"

    started, enqueue = _boot(redis)

    assert started
    assert enqueue.call_args.args[1] is None


def test_each_batch_records_progress_and_names_its_successor_for_the_boot():
    """The successor's job id is what the boot looks for, so a chain that is
    alive is never doubled, and one that died is resumed where it stopped."""
    redis = _FakeRedis()
    fake_queue = _FakeQueue()
    key = mod._APPLIED_KEY.format(1)
    middle = mod.ReanalyzeBatchResult(processed=[3, 7], next_cursor=7, remaining=1)
    last = mod.ReanalyzeBatchResult(processed=[9], next_cursor=None, remaining=0)

    def run(result, cursor):
        with patch("app.core.queue.redis_conn", redis), patch(
            "app.core.queue.queue", fake_queue
        ), patch.object(mod, "SessionLocal", return_value=MagicMock()), patch.object(
            mod, "reanalyze_history_batch", return_value=result
        ):
            mod.reanalyze_history_job(cursor, None, 2, 1)

    run(middle, None)
    assert redis.store[key] == "at:7"
    assert fake_queue.waiting[-1][1] == (7, None, 2, 1)
    assert _boot(redis, fake_queue=fake_queue)[0] is False  # alive: left alone

    fake_queue.waiting.clear()  # the worker died before running the successor
    started, enqueue = _boot(redis, fake_queue=fake_queue)
    assert started and enqueue.call_args.args[1] == 7

    run(last, 7)
    assert redis.store[key] == "done"


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


def test_a_failed_enqueue_of_a_fresh_pass_leaves_it_for_the_next_boot():
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
    sched.assert_called_once_with(1, None, 20, None)


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
