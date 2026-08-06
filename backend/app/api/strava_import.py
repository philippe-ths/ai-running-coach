from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import DbSession, LinkedStravaAccount
from app.models import StravaImport
from app.schemas import StravaImportCreate, StravaImportRead

router = APIRouter()


def validated_import_request(payload: StravaImportCreate) -> StravaImportCreate:
    """Reject a future `since_date` (400).

    A dependency rather than a body check, and declared BEFORE the account
    dependency, because the order is observable: a caller with no linked Strava
    account who posts a future date has always been told about the date (400),
    not about the account (404). Dependencies resolve in declaration order, so
    the precedence is preserved by where this sits in the signature.
    """
    if payload.since_date > date.today():
        raise HTTPException(
            status_code=400, detail="since_date cannot be in the future."
        )
    return payload


ValidatedImportRequest = Annotated[
    StravaImportCreate, Depends(validated_import_request)
]


@router.post("/strava/import", response_model=StravaImportRead)
def start_import(
    payload: ValidatedImportRequest,
    account: LinkedStravaAccount,
    db: DbSession,
):
    """Start a resumable historical Strava import from `since_date` to today.

    Imports activity summaries + deterministic analysis only (no streams, no AI
    coach report, no notifications); the work runs in a self-pacing background
    job. Idempotent: if an import is already running for this account, that one
    is returned instead of starting a second walk.
    """
    from app.jobs.strava_import import enqueue_import

    import_obj = enqueue_import(db, account, payload.since_date)
    return import_obj


@router.get("/strava/import/status", response_model=Optional[StravaImportRead])
def import_status(
    account: LinkedStravaAccount,
    db: DbSession,
):
    """Return the most recent historical import for the account, or null if none.

    The frontend polls this to show import progress and the final summary.
    """
    latest = (
        db.query(StravaImport)
        .filter(StravaImport.user_id == account.user_id)
        .order_by(StravaImport.created_at.desc())
        .first()
    )
    return latest
