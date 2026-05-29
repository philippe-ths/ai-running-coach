"""DATABASE_URL driver-prefix normalisation.

Oracle: SQLAlchemy + psycopg (v3) require the ``postgresql+psycopg://`` driver
prefix. Railway's managed Postgres hands out a bare ``postgresql://`` URL, and
some providers emit the legacy ``postgres://`` scheme that SQLAlchemy rejects
outright. ``Settings`` normalises these so the engine and Alembic always get a
driver-qualified URL.
"""

from app.core.config import Settings


def _settings(url: str) -> Settings:
    return Settings(DATABASE_URL=url)


def test_bare_postgresql_url_gets_psycopg_driver():
    s = _settings("postgresql://user:pass@host:5432/db")
    assert s.DATABASE_URL == "postgresql+psycopg://user:pass@host:5432/db"


def test_legacy_postgres_scheme_normalised():
    s = _settings("postgres://user:pass@host:5432/db")
    assert s.DATABASE_URL == "postgresql+psycopg://user:pass@host:5432/db"


def test_already_psycopg_url_left_unchanged():
    url = "postgresql+psycopg://user:pass@host:5432/db"
    assert _settings(url).DATABASE_URL == url


def test_other_named_driver_left_unchanged():
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    assert _settings(url).DATABASE_URL == url
