"""Real-Postgres concurrency proof for the atomic Exchange claim transitions (#506).

`claim_fuller` and `mark_done` in `app/services/coach/exchange_lifecycle.py` are
the at-most-once guarantees for the fuller turn and the "done" tap. They are
written as atomic conditional UPDATEs (`... WHERE <sentinel> IS NULL`, then a
rowcount check) precisely so the DATABASE serializes racing triggers. The default
unit suite only exercises them against in-memory SQLite on a single connection,
which cannot exercise true multi-connection row locking.

This integration test runs the REAL `claim_fuller` / `mark_done` (imported from the
prod module, not reimplemented) against a real local Postgres instance, from many
separate connections hammering one row at the same instant, and asserts that
EXACTLY ONE caller wins each race. It is marked `integration` so it stays out of
the default `make backend-test` run (pyproject addopts uses `-m "not integration"`),
and it SKIPS gracefully when Postgres is unreachable so it can never break CI.

Threads (not processes) are used, but each thread holds its OWN SQLAlchemy Session
on its OWN DB connection, so the contention is resolved inside Postgres (row-level
locking / MVCC), not by the Python GIL. A `threading.Barrier` releases all workers
at the same instant to maximise the overlap window.
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base

# Force every ORM model to register on Base.metadata before create_all.
import app.models  # noqa: F401
from app.models import Activity, Block, Exchange, User
from app.services.coach.exchange_lifecycle import claim_fuller, mark_done

# --- environment -------------------------------------------------------------

_PG_HOST = "localhost"
_PG_PORT = 5433
_PG_USER = "coach"
_PG_PASSWORD = "coach"
_ADMIN_DB = "coach"  # an existing db to issue CREATE DATABASE from
_TEST_DB = "coach_concurrency_test"

_ADMIN_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_ADMIN_DB}"
)
_TEST_URL = (
    f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_TEST_DB}"
)

_TRIALS = 30
_THREADS = 16


def _pg_reachable() -> bool:
    try:
        eng = create_engine(_ADMIN_URL, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def pg_engine():
    """A dedicated engine against a throwaway database on the local PG instance.

    Creates the database (dropping a stale one first), materializes the full ORM
    schema, yields a sessionmaker, then drops the database so the dev `coach`
    database and its seeded data are never touched.
    """
    if not _pg_reachable():
        pytest.skip("local Postgres not reachable at localhost:5433")

    admin = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": _TEST_DB},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{_TEST_DB}"'))
    admin.dispose()

    engine = create_engine(_TEST_URL, pool_size=_THREADS + 4, max_overflow=_THREADS)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine, Session
    finally:
        engine.dispose()
        admin = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": _TEST_DB},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB}"'))
        admin.dispose()


def _seed_exchange(Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Build a minimal real object graph (User -> Activity -> Block -> Exchange)
    with both claim sentinels NULL, and return (exchange_id, block_id)."""
    db = Session()
    try:
        now = datetime.now(timezone.utc)
        user = User(email=f"race-{uuid.uuid4()}@example.test")
        db.add(user)
        db.flush()

        activity = Activity(
            user_id=user.id,
            strava_activity_id=int(uuid.uuid4().int % 9_000_000_000) + 1,
            start_date=now,
            type="Run",
            name="race seed",
            distance_m=5000,
            moving_time_s=1500,
            elapsed_time_s=1500,
        )
        db.add(activity)
        db.flush()

        block = Block(
            user_id=user.id,
            start_date=now,
            end_date=now + timedelta(minutes=30),
            primary_activity_id=activity.id,
        )
        db.add(block)
        db.flush()

        activity.block_id = block.id
        db.add(activity)
        db.flush()

        exchange = Exchange(
            user_id=user.id,
            block_id=block.id,
            opened_at=now,  # open: replies/done act on an open exchange
        )
        db.add(exchange)
        db.commit()
        return exchange.id, block.id
    finally:
        db.close()


def _run_race(Session, claim_fn, exchange_id: uuid.UUID) -> list[bool]:
    """Launch _THREADS workers, each on its OWN Session/connection, synchronized on
    a Barrier so they all invoke `claim_fn(db, exchange)` at the same instant.
    Returns each worker's boolean result."""
    barrier = threading.Barrier(_THREADS)
    results: list[bool] = [False] * _THREADS
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        db = Session()
        try:
            exchange = db.get(Exchange, exchange_id)
            barrier.wait()  # release all threads together
            results[idx] = claim_fn(db, exchange)
        except BaseException as exc:  # noqa: BLE001 - surface, don't swallow
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker raised: {errors!r}"
    return results


@pytest.mark.integration
def test_claim_fuller_exactly_one_winner_under_concurrent_pg_connections(pg_engine):
    _engine, Session = pg_engine

    winners_per_trial = []
    for trial in range(_TRIALS):
        exchange_id, _ = _seed_exchange(Session)

        results = _run_race(Session, claim_fuller, exchange_id)
        wins = sum(results)
        winners_per_trial.append(wins)
        assert wins == 1, (
            f"claim_fuller trial {trial}: expected exactly 1 winner across "
            f"{_THREADS} concurrent PG connections, got {wins} ({results})"
        )

        # The row is claimed exactly once: fuller_sent_at is now set (non-null).
        db = Session()
        try:
            ex = db.get(Exchange, exchange_id)
            assert ex.fuller_sent_at is not None, "winner must have set fuller_sent_at"
            # A fresh claim AFTER a win must lose (the sentinel is already set).
            assert claim_fuller(db, ex) is False, "post-win claim must return False"
        finally:
            db.close()

    assert all(w == 1 for w in winners_per_trial)
    print(
        f"\nclaim_fuller: {sum(1 for w in winners_per_trial if w == 1)}/{_TRIALS} "
        f"trials had EXACTLY ONE winner across {_THREADS} concurrent PG connections."
    )


@pytest.mark.integration
def test_mark_done_exactly_one_winner_under_concurrent_pg_connections(pg_engine):
    _engine, Session = pg_engine

    winners_per_trial = []
    for trial in range(_TRIALS):
        exchange_id, _ = _seed_exchange(Session)

        results = _run_race(Session, mark_done, exchange_id)
        wins = sum(results)
        winners_per_trial.append(wins)
        assert wins == 1, (
            f"mark_done trial {trial}: expected exactly 1 winner across "
            f"{_THREADS} concurrent PG connections, got {wins} ({results})"
        )

        db = Session()
        try:
            ex = db.get(Exchange, exchange_id)
            assert ex.done_at is not None, "winner must have set done_at"
            assert mark_done(db, ex) is False, "post-win mark_done must return False"
        finally:
            db.close()

    assert all(w == 1 for w in winners_per_trial)
    print(
        f"\nmark_done: {sum(1 for w in winners_per_trial if w == 1)}/{_TRIALS} "
        f"trials had EXACTLY ONE winner across {_THREADS} concurrent PG connections."
    )
