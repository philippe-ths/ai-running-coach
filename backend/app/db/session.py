from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Construct the database URL. 
# We expect postgresql+psycopg:// from the environment.
# If using standard postgres:// we might need string replacement if the driver demands it,
# but for now we assume the .env is correct.

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # Echo SQL in local environment for debugging
    echo=(settings.APP_ENV == "local") 
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency for FastAPI path operations."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
