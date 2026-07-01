from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings

# Construct the database URL.
# We expect postgresql+psycopg:// from the environment (Settings normalises the
# driver prefix). If using standard postgres:// the config validator rewrites it.


def _build_engine(db_settings: Settings) -> Engine:
    """Build the SQLAlchemy engine with explicit, env-driven pool sizing (#605).

    ``pool_pre_ping`` (checks a connection is alive before handing it out) always
    applies. The QueuePool sizing (``pool_size``/``max_overflow``/
    ``pool_recycle``/``pool_timeout``) is applied ONLY for a real pooled backend
    (Postgres). SQLite (the test suite's in-memory DB) uses a different pool that
    rejects those kwargs, so they are omitted there.
    """
    url = db_settings.DATABASE_URL
    kwargs: dict = {
        "pool_pre_ping": True,
        # Echo SQL in local environment for debugging.
        "echo": db_settings.APP_ENV == "local",
    }
    if make_url(url).get_backend_name() != "sqlite":
        kwargs.update(
            pool_size=db_settings.DB_POOL_SIZE,
            max_overflow=db_settings.DB_MAX_OVERFLOW,
            pool_recycle=db_settings.DB_POOL_RECYCLE,
            pool_timeout=db_settings.DB_POOL_TIMEOUT,
        )
    return create_engine(url, **kwargs)


engine = _build_engine(settings)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency for FastAPI path operations."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
