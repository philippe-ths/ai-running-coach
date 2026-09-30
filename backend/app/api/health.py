import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import get_db

router = APIRouter()

@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Checks if the app is running and DB is reachable.
    """
    try:
        # Simple query to check DB connection
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "database": db_status,
        # The commit this deployment was built from, so the post-deploy gate can
        # tell it from the previous deployment, which also answers healthy while
        # this one builds (#1027). Railway sets it on GitHub-triggered deploys;
        # null anywhere else. The repository is public, so it discloses nothing.
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
    }
