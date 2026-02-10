"""
Pydantic schemas for the /api/trends endpoint.
"""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class WeeklyDistancePoint(BaseModel):
    week_start: date
    total_distance_m: int
    activity_count: int


class WeeklyTimePoint(BaseModel):
    week_start: date
    total_moving_time_s: int
    activity_count: int


class DailyDistancePoint(BaseModel):
    date: date
    total_distance_m: int
    activity_count: int


class DailyTimePoint(BaseModel):
    date: date
    total_moving_time_s: int
    activity_count: int


class PaceTrendPoint(BaseModel):
    date: date
    pace_sec_per_km: float
    type: str


class SufferScorePoint(BaseModel):
    date: date
    effort_score: float
    type: str


class TrendsSummary(BaseModel):
    total_distance_m: int
    total_moving_time_s: int
    activity_count: int


class TrendsResponse(BaseModel):
    range: str
    summary: TrendsSummary
    weekly_distance: List[WeeklyDistancePoint]
    weekly_time: List[WeeklyTimePoint]
    daily_distance: List[DailyDistancePoint]
    daily_time: List[DailyTimePoint]
    pace_trend: List[PaceTrendPoint]
    suffer_score: List[SufferScorePoint]
