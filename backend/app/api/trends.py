"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trends import (
    TrendsResponse,
    WeeklyDistancePoint,
    WeeklyTimePoint,
    PaceTrendPoint,
)
from app.services.trends import (
    build_activity_facts,
    build_daily_facts,
    build_weekly_buckets,
    build_pace_trend,
    get_available_types,
)

router = APIRouter()

ALLOWED_RANGES = {"7D", "30D", "3M", "6M", "1Y", "ALL"}


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
    range_upper = range.upper()
    if range_upper not in ALLOWED_RANGES:
        range_upper = "30D"

    # 1. Activity-level facts (filtered by types if provided)
    activity_facts = build_activity_facts(db, range_upper, types=types)

    # 2. Daily facts (sum per local date)
    daily_facts = build_daily_facts(activity_facts)

    # 3. Weekly buckets
    weekly = build_weekly_buckets(daily_facts)

    weekly_distance = [
        WeeklyDistancePoint(
            week_start=w.week_start,
            total_distance_m=w.total_distance_m,
            activity_count=w.activity_count,
        )
        for w in weekly
    ]

    weekly_time = [
        WeeklyTimePoint(
            week_start=w.week_start,
            total_moving_time_s=w.total_moving_time_s,
            activity_count=w.activity_count,
        )
        for w in weekly
    ]

    # 4. Pace trend (per-activity, run/walk only)
    pace_trend = [
        PaceTrendPoint(**p) for p in build_pace_trend(activity_facts)
    ]

    return TrendsResponse(
        range=range_upper,
        weekly_distance=weekly_distance,
        weekly_time=weekly_time,
        pace_trend=pace_trend,
    )
