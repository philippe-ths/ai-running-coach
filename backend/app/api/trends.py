"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.trends import (
    LoadResponse,
    TrendsResponse,
    VolumeReport,
    WeeklyStatsResponse,
)
from app.services.training_load import get_load_report
from app.services.trends import (
    get_available_types,
    get_trends_report,
    get_volume_report,
    get_weekly_stats,
)

router = APIRouter()


@router.get("/trends/types", response_model=List[str])
def list_activity_types(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Return distinct activity types available for filtering."""
    return get_available_types(db, user_id=user.id)


@router.get("/trends", response_model=TrendsResponse)
def get_trends(
    range: str = Query("30D", description="Time range: 7D, 30D, 3M, 6M, 1Y, ALL"),
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    mode: str = Query("rolling", description="Window framing: rolling | calendar (#400)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    return get_trends_report(db, range, types, user_id=user.id, mode=mode)


@router.get("/trends/load", response_model=LoadResponse)
def get_training_load(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Weekly training-load report: scores, optimal band, contributions (#209)."""
    return get_load_report(db, user_id=user.id)


@router.get("/trends/volume", response_model=VolumeReport)
def get_volume(
    range: str = Query("7D", description="7D | 30D | 3M | 6M | 1Y"),
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Frequency-/volume-vs-norm report for the selected range, as of today:
    per-metric current vs the runner's norm, in rolling and calendar-period
    framings scaled to the range (#400). ``types`` scopes both the window and the
    norm baseline so the comparison stays like-for-like with the filtered Trends
    charts (#413)."""
    return get_volume_report(db, user.id, range, types=types)


@router.get("/stats/weekly", response_model=WeeklyStatsResponse)
def get_dashboard_weekly_stats(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Rolling 7-day dashboard summary with the prior 7 days for comparison (#246, #248)."""
    return get_weekly_stats(db, user_id=user.id)
