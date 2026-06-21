import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.main import app
from app.db.session import get_db
from app.db.base import Base


@pytest.fixture(autouse=True)
def _isolate_notification_channels(monkeypatch):
    """Pin notification-channel settings off by default.

    Channel selection (`_active_channel`) reads `settings`, which loads from
    `backend/.env`. Without this, a developer `.env` that has Telegram or SMTP
    configured leaks into tests and flips channel selection, so the email-path
    tests fail (and CI passes only because CI has none of these set). Resetting
    them here makes every test start from "no channel configured"; tests that
    need a channel opt in via their own monkeypatch. Autouse fixtures run before
    a test's explicitly-requested fixtures, so per-test overrides still win.
    """
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_HOST", "NOTIFY_TO"):
        monkeypatch.setattr(settings, var, "")

# Use an in-memory SQLite database for tests, or a separate test DB
# For simplicity with SQLAlchemy features, an in-memory SQLite is easiest 
# but might have dialect differences with Postgres.
# Given we are testing routers primarily, it might be safer to mock the DB or specific functions 
# IF the logic is complex. But for basics, SQLite is usually fine.
# HOWEVER, since we used Postgres-specifics (like JSONB) earlier or might, 
# let's try to stick to mocking the session or using the real DB if configured for test.

# For this specific task (Webhooks), we are just saving/updating, so SQLite is fine.
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session")
def db_engine():
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def db(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def override_get_db(db):
    """
    Explicitly return the override function if needed manually,
    but normally 'client' fixture handles the override.
    """
    return db


@pytest.fixture
def enable_durable_memory(monkeypatch):
    """Turn the prior-report-driven coaching loops back on for the tests that
    exercise that machinery: the M8 belief loop, the A2c narrative, the M4
    longitudinal prior-report digest, and the M7 adherence section. All default
    OFF in prod (the belief loop misclassified rest-day advice and poisoned a
    belief; the narrative and the prior-report digest replayed that theme forward
    run after run), so these feature tests opt in explicitly rather than relying
    on the disabled default.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "COACH_BELIEFS_ENABLED", True)
    monkeypatch.setattr(settings, "COACH_NARRATIVE_ENABLED", True)
    monkeypatch.setattr(settings, "COACH_PRIOR_REPORTS_ENABLED", True)
    monkeypatch.setattr(settings, "COACH_ADHERENCE_ENABLED", True)
