"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trends import LoadResponse, TrendsResponse, WeeklyStatsResponse
from app.services.training_load import get_load_report
from app.services.trends import (
    get_available_types,
    get_trends_report,
    get_weekly_stats,
)

router = APIRouter()


@router.get("/trends/types", response_model=List[str])
def list_activity_types(db: Session = Depends(get_db)):
    """Return distinct activity types available for filtering."""
    return get_available_types(db)


@router.get("/trends", response_model=TrendsResponse)
def get_trends(
    range: str = Query("30D", description="Time range: 7D, 30D, 3M, 6M, 1Y, ALL"),
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    db: Session = Depends(get_db),
):
    return get_trends_report(db, range, types)


@router.get("/trends/load", response_model=LoadResponse)
def get_training_load(db: Session = Depends(get_db)):
    """Weekly training-load report: scores, optimal band, contributions (#209)."""
    return get_load_report(db)


@router.get("/stats/weekly", response_model=WeeklyStatsResponse)
def get_dashboard_weekly_stats(db: Session = Depends(get_db)):
    """Rolling 7-day dashboard summary with the prior 7 days for comparison (#246, #248)."""
    return get_weekly_stats(db)
