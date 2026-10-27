import os

# Pin the session to CI's environment BEFORE any app import (#752). `Settings` is
# a module-level singleton built the moment `app.core.config` is imported, so
# these must be set first — hence the imports below rather than at the top.
#
# A developer `backend/.env` carries prod-parity coach configuration (a lean
# prompt id, the receipt cadence on, most COACH_*_ENABLED switches off) while a
# large family of tests asserts the byte-stable behaviour of the DEFAULTS. That
# disagreement made `make backend-test` report 99 failures locally and 0 in CI.
# Opting out of the env file makes a local run resolve exactly what CI resolves.
#
# This covers the ENV FILE only. A variable exported in the developer's SHELL
# still wins, which is why the notification and Clerk fixtures below remain.
os.environ.setdefault("RUNNING_COACH_SKIP_DOTENV", "1")
# The one setting with no default. CI supplies the same dummy; tests run against
# the in-memory SQLite engine created below, so this URL is never dialled.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://test:test@localhost/test"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_notification_channels(monkeypatch):
    """Pin notification-channel settings off by default.

    Channel selection (`_active_channel`) reads `settings`, which loads from
    `backend/.env`. Without this, a developer `.env` that has Telegram configured
    leaks into tests and flips channel selection, so the no-channel tests fail
    (and CI passes only because CI has none of these set). Resetting them here
    makes every test start from "no channel configured"; tests that need a channel
    opt in via their own monkeypatch. Autouse fixtures run before a test's
    explicitly-requested fixtures, so per-test overrides still win.
    """
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setattr(settings, var, "")


@pytest.fixture(autouse=True)
def _disable_clerk_auth(monkeypatch):
    """Run the suite in the Clerk-disabled single-user path by default.

    `settings` loads `backend/.env`, which on a developer machine now carries a
    real CLERK_JWKS_URL. Left in place that would flip verify_clerk_session into
    enforcement and 401 every existing endpoint test. Resetting the Clerk
    settings here makes every test start from "Clerk not configured" (the
    degrade-to-single-user path, identical to pre-Phase-2 behaviour). The auth
    tests opt back in via their own monkeypatch + injected verifier.
    """
    from app.core import clerk_auth

    for var in ("CLERK_JWKS_URL", "CLERK_SECRET_KEY", "CLERK_ISSUER",
                "CLERK_AUTHORIZED_PARTIES", "OWNER_EMAIL"):
        monkeypatch.setattr(settings, var, "")
    monkeypatch.setattr(settings, "APP_ENV", "local")
    clerk_auth.reset_verifier_cache()


@pytest.fixture(autouse=True)
def _isolate_llm_budget():
    """Pin the P2.2 LLM budget gate to a fresh in-process backend per test.

    The coach generation path now consults the budget gate, which otherwise
    lazily builds a Redis-backed gate (polluting a developer's local Redis and
    leaking spend across tests). An in-memory gate plus the all-zero default
    ceilings makes the gate a no-op for every test that does not opt in; the cost
    cap tests inject their own capped gate.
    """
    from app.services.coach import budget

    budget.set_gate(budget.new_in_memory_gate())
    yield
    budget.set_gate(None)


@pytest.fixture(autouse=True)
def _refuse_real_strava_requests(request, monkeypatch):
    """Fail any test that would send a real HTTP request to Strava (#1078).

    A test that forgets to inject a Strava port falls through to the HTTP
    adapter, and its result then depends on how a live request with a fake
    token fails. Patching the real transports leaves `httpx.MockTransport`
    untouched, so adapter tests that mock the wire still run.
    """
    if request.node.get_closest_marker("integration"):
        return
    import httpx

    def _refuse(req):
        if req.url.host.endswith("strava.com"):
            raise RuntimeError(
                f"Test sent a real request to Strava: {req.method} {req.url}. "
                "Inject an InMemoryStravaAdapter."
            )

    sync_send = httpx.HTTPTransport.handle_request
    async_send = httpx.AsyncHTTPTransport.handle_async_request

    def _sync(self, req):
        _refuse(req)
        return sync_send(self, req)

    async def _async(self, req):
        _refuse(req)
        return await async_send(self, req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _async)

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
def savepoint_db(db_engine):
    """A session whose commits are savepoints inside the test's transaction.

    The `db` session is bound to an outer transaction it cannot leave, so a
    `db.rollback()` in the code under test discards everything the test seeded
    and leaks later writes past the test. Code whose recovery path rolls back
    after a database error (#1136) is tested on this session instead, where a
    rollback undoes only the work since the last commit, as on a real database.
    """
    connection = db_engine.connect()
    # pysqlite does not emit BEGIN before a SAVEPOINT, so releasing the first
    # savepoint would COMMIT for real and leak rows into later tests. Take the
    # transaction by hand, with the driver's own transaction handling off.
    raw = connection.connection.dbapi_connection
    isolation = raw.isolation_level
    raw.isolation_level = None
    connection.exec_driver_sql("BEGIN")
    session = TestingSessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        connection.exec_driver_sql("ROLLBACK")
        raw.isolation_level = isolation
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
    exercise that machinery: the M4 longitudinal prior-report digest and the M7
    adherence section. Both default OFF in prod (the prior-report digest replayed a
    stale theme forward run after run), so these feature tests opt in explicitly
    rather than relying on the disabled default. (The M8 belief loop + A2c narrative
    halves of durable memory were retired in M4, ADR 0025, replaced by the runner
    memory profile; their switches are gone.)
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "COACH_PRIOR_REPORTS_ENABLED", True)
    monkeypatch.setattr(settings, "COACH_ADHERENCE_ENABLED", True)
