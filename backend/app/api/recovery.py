"""API router for /api/recovery: the runner's stored nights (#555)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.recovery import RecoveryResponse
from app.services.recovery import store

router = APIRouter()


@router.get("/recovery", response_model=RecoveryResponse)
def get_recovery(
    user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365, description="How many days back to return"),
):
    """The signed-in runner's own recovery nights, newest first.

    Scoped to the caller: a runner whose device was never synced (every runner but
    the deployment owner today) gets an empty list, never someone else's nights.
    """
    since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    return RecoveryResponse(days=store.list_recent(db, user.id, since=since))
