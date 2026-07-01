"""Unit tests for the worker runtime selection (#594).

The worker can run either a single in-process ``Worker`` (default, byte-identical
to today) or an RQ ``WorkerPool`` of N forked workers, chosen by
``WORKER_POOL_SIZE``. These tests pin the size default, its validation, and the
type/size the builder selects for each branch. Construction is exercised against
a version-configured ``MagicMock`` connection so no live Redis is required.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from rq import Worker
from rq.worker_pool import WorkerPool

from app.core.config import Settings
from app.worker import build_worker_runtime


def _fake_conn() -> MagicMock:
    """A MagicMock that satisfies rq.Worker's Redis-version probe on construct."""
    conn = MagicMock()
    conn.info.return_value = {"redis_version": "7.0.0"}
    # A real dict so rq's socket_timeout probe gets None, not a MagicMock.
    conn.connection_pool.connection_kwargs = {}
    return conn


def _settings(size: int) -> SimpleNamespace:
    return SimpleNamespace(WORKER_POOL_SIZE=size)


def test_worker_pool_size_defaults_to_one() -> None:
    s = Settings(DATABASE_URL="postgresql://u:p@localhost/db")
    assert s.WORKER_POOL_SIZE == 1


def test_worker_pool_size_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="postgresql://u:p@localhost/db", WORKER_POOL_SIZE=0)


def test_builder_returns_single_worker_on_default() -> None:
    runtime = build_worker_runtime(_settings(1), _fake_conn())
    assert isinstance(runtime, Worker)
    assert not isinstance(runtime, WorkerPool)
    assert [q.name for q in runtime.queues] == ["default"]


def test_builder_returns_worker_pool_when_size_above_one() -> None:
    runtime = build_worker_runtime(_settings(3), _fake_conn())
    assert isinstance(runtime, WorkerPool)
    assert runtime.num_workers == 3
    assert list(runtime._queue_names) == ["default"]
