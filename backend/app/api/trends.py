"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.profile import get_current_user_profile
from app.db.session import get_db
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
def list_activity_types(db: Session = Depends(get_db)):
    """Return distinct activity types available for filtering."""
    user_id = get_current_user_profile(db).user_id
    return get_available_types(db, user_id=user_id)


@router.get("/trends", response_model=TrendsResponse)
def get_trends(
    range: str = Query("30D", description="Time range: 7D, 30D, 3M, 6M, 1Y, ALL"),
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    db: Session = Depends(get_db),
):
    user_id = get_current_user_profile(db).user_id
    return get_trends_report(db, range, types, user_id=user_id)


@router.get("/trends/load", response_model=LoadResponse)
def get_training_load(db: Session = Depends(get_db)):
    """Weekly training-load report: scores, optimal band, contributions (#209)."""
    return get_load_report(db)


@router.get("/trends/volume", response_model=VolumeReport)
def get_volume(
    range: str = Query("7D", description="7D | 30D | 3M | 6M | 1Y"),
    db: Session = Depends(get_db),
):
    """Frequency-/volume-vs-norm report for the selected range, as of today:
    per-metric current vs the runner's norm, in rolling and calendar-period
    framings scaled to the range (#400)."""
    user_id = get_current_user_profile(db).user_id
    return get_volume_report(db, user_id, range)


@router.get("/stats/weekly", response_model=WeeklyStatsResponse)
def get_dashboard_weekly_stats(db: Session = Depends(get_db)):
    """Rolling 7-day dashboard summary with the prior 7 days for comparison (#246, #248)."""
    user_id = get_current_user_profile(db).user_id
    return get_weekly_stats(db, user_id=user_id)
