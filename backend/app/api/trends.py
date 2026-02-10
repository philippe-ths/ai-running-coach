"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trends import (
    TrendsResponse,
    TrendsSummary,
    WeeklyDistancePoint,
    WeeklyTimePoint,
    DailyDistancePoint,
    DailyTimePoint,
    PaceTrendPoint,
    SufferScorePoint,
    DailySufferScorePoint,
)
from app.services.trends import (
    build_activity_facts,
    build_daily_facts,
    build_continuous_daily_facts,
    build_weekly_buckets,
    build_pace_trend,
    build_suffer_score_trend,
    build_continuous_suffer_scores,
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

    # Summary totals across the entire range
    summary = TrendsSummary(
        total_distance_m=sum(d.total_distance_m for d in daily_facts),
        total_moving_time_s=sum(d.total_moving_time_s for d in daily_facts),
        activity_count=sum(d.activity_count for d in daily_facts),
    )

    # 3. Continuous daily facts (every day filled)
    continuous_daily = build_continuous_daily_facts(daily_facts, range_key=range_upper)

    daily_distance = [
        DailyDistancePoint(
            date=d.local_date,
            total_distance_m=d.total_distance_m,
            activity_count=d.activity_count,
        )
        for d in continuous_daily
    ]

    daily_time = [
        DailyTimePoint(
            date=d.local_date,
            total_moving_time_s=d.total_moving_time_s,
            activity_count=d.activity_count,
        )
        for d in continuous_daily
    ]

    # 4. Weekly buckets (continuous — includes empty weeks)
    weekly = build_weekly_buckets(daily_facts, range_key=range_upper)

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

    # 5. Suffer score (per-activity)
    suffer_score = [
        SufferScorePoint(**p) for p in build_suffer_score_trend(activity_facts)
    ]

    # 6. Daily suffer score (continuous — every day filled)
    daily_suffer_score = [
        DailySufferScorePoint(**p)
        for p in build_continuous_suffer_scores(activity_facts, range_key=range_upper)
    ]

    return TrendsResponse(
        range=range_upper,
        summary=summary,
        weekly_distance=weekly_distance,
        weekly_time=weekly_time,
        daily_distance=daily_distance,
        daily_time=daily_time,
        pace_trend=pace_trend,
        suffer_score=suffer_score,
        daily_suffer_score=daily_suffer_score,
    )
