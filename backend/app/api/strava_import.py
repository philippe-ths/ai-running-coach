from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import StravaAccount, StravaImport
from app.schemas import StravaImportCreate, StravaImportRead

router = APIRouter()


def _resolve_account(db: Session, strava_athlete_id: Optional[int]) -> StravaAccount:
    if strava_athlete_id:
        account = (
            db.query(StravaAccount)
            .filter(StravaAccount.strava_athlete_id == strava_athlete_id)
            .first()
        )
    else:
        # Single-player mode: default to the only connected account.
        account = db.query(StravaAccount).first()
    if not account:
        raise HTTPException(
            status_code=404,
            detail="No linked Strava account found. Connect Strava first.",
        )
    return account


@router.post("/strava/import", response_model=StravaImportRead)
def start_import(
    payload: StravaImportCreate,
    strava_athlete_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Start a resumable historical Strava import from `since_date` to today.

    Imports activity summaries + deterministic analysis only (no streams, no AI
    coach report, no notifications); the work runs in a self-pacing background
    job. Idempotent: if an import is already running for this account, that one
    is returned instead of starting a second walk.
    """
    if payload.since_date > date.today():
        raise HTTPException(status_code=400, detail="since_date cannot be in the future.")

    account = _resolve_account(db, strava_athlete_id)

    from app.jobs.strava_import import enqueue_import

    import_obj = enqueue_import(db, account, payload.since_date)
    return import_obj


@router.get("/strava/import/status", response_model=Optional[StravaImportRead])
def import_status(
    strava_athlete_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return the most recent historical import for the account, or null if none.

    The frontend polls this to show import progress and the final summary.
    """
    account = _resolve_account(db, strava_athlete_id)
    latest = (
        db.query(StravaImport)
        .filter(StravaImport.user_id == account.user_id)
        .order_by(StravaImport.created_at.desc())
        .first()
    )
    return latest
