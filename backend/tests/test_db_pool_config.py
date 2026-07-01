"""Explicit, env-driven connection-pool sizing on the SQLAlchemy engine (#605).

The engine must apply the configured QueuePool sizing for a real Postgres
backend, so web + worker processes do not each open the implicit default pool
(5 persistent + 10 overflow = 15 connections) against one managed Postgres.
The SQLite path (tests, in-memory) must be left untouched, because SQLite's
pool rejects ``pool_size``/``max_overflow``/``pool_timeout``.
"""

import app.db.session as session_module
from app.core.config import Settings


def _settings(url: str, **overrides) -> Settings:
    return Settings(DATABASE_URL=url, **overrides)


def _capture_create_engine(monkeypatch) -> dict:
    captured: dict = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    return captured


def test_pool_settings_have_conservative_defaults():
    s = _settings("postgresql+psycopg://u:p@h:5432/db")
    assert s.DB_POOL_SIZE == 5
    assert s.DB_MAX_OVERFLOW == 5
    assert s.DB_POOL_RECYCLE == 1800
    assert s.DB_POOL_TIMEOUT == 30


def test_postgres_engine_gets_configured_pool(monkeypatch):
    captured = _capture_create_engine(monkeypatch)
    s = _settings(
        "postgresql+psycopg://u:p@h:5432/db",
        DB_POOL_SIZE=7,
        DB_MAX_OVERFLOW=3,
        DB_POOL_RECYCLE=900,
        DB_POOL_TIMEOUT=15,
    )

    session_module._build_engine(s)

    kwargs = captured["kwargs"]
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_recycle"] == 900
    assert kwargs["pool_timeout"] == 15


def test_sqlite_engine_omits_queue_pool_args(monkeypatch):
    captured = _capture_create_engine(monkeypatch)
    s = _settings("sqlite:///:memory:", DB_POOL_SIZE=7)

    session_module._build_engine(s)

    kwargs = captured["kwargs"]
    # pre-ping is safe and stays; queue-pool sizing must NOT be passed to SQLite.
    assert kwargs["pool_pre_ping"] is True
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    assert "pool_timeout" not in kwargs


def test_real_postgres_engine_pool_is_bounded():
    """Build a real (non-connecting) engine and confirm the live pool is bounded."""
    engine = session_module._build_engine(
        _settings(
            "postgresql+psycopg://u:p@h:5432/db",
            DB_POOL_SIZE=4,
            DB_MAX_OVERFLOW=2,
        )
    )
    # QueuePool.size() reports the configured persistent-connection count.
    assert engine.pool.size() == 4
