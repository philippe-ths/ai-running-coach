from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models import StravaAccount, StravaImport, User
from app.schemas import StravaImportCreate, StravaImportRead

router = APIRouter()


def _resolve_account(db: Session, user: User) -> StravaAccount:
    # P2.1: the authenticated user's own Strava account, never the first found.
    account = (
        db.query(StravaAccount)
        .filter(StravaAccount.user_id == user.id)
        .first()
    )
    if not account:
        raise HTTPException(
            status_code=404,
            detail="No linked Strava account found. Connect Strava first.",
        )
    return account


@router.post("/strava/import", response_model=StravaImportRead)
def start_import(
    payload: StravaImportCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Start a resumable historical Strava import from `since_date` to today.

    Imports activity summaries + deterministic analysis only (no streams, no AI
    coach report, no notifications); the work runs in a self-pacing background
    job. Idempotent: if an import is already running for this account, that one
    is returned instead of starting a second walk.
    """
    if payload.since_date > date.today():
        raise HTTPException(status_code=400, detail="since_date cannot be in the future.")

    account = _resolve_account(db, user)

    from app.jobs.strava_import import enqueue_import

    import_obj = enqueue_import(db, account, payload.since_date)
    return import_obj


@router.get("/strava/import/status", response_model=Optional[StravaImportRead])
def import_status(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Return the most recent historical import for the account, or null if none.

    The frontend polls this to show import progress and the final summary.
    """
    account = _resolve_account(db, user)
    latest = (
        db.query(StravaImport)
        .filter(StravaImport.user_id == account.user_id)
        .order_by(StravaImport.created_at.desc())
        .first()
    )
    return latest
