import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.session import get_db
from app.db.base import Base

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
