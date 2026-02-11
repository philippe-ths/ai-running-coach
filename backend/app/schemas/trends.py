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


class SufferScorePoint(BaseModel):
    date: date
    effort_score: float
    type: str


class DailySufferScorePoint(BaseModel):
    date: date
    effort_score: float


class WeeklySufferScorePoint(BaseModel):
    week_start: date
    effort_score: float


class EfficiencyPoint(BaseModel):
    date: date
    efficiency_mps_per_bpm: float
    type: str


class TrendsSummary(BaseModel):
    total_distance_m: int
    total_moving_time_s: int
    activity_count: int
    total_suffer_score: float


class TrendsResponse(BaseModel):
    range: str
    summary: TrendsSummary
    previous_summary: Optional[TrendsSummary] = None
    weekly_distance: List[WeeklyDistancePoint]
    weekly_time: List[WeeklyTimePoint]
    weekly_suffer_score: List[WeeklySufferScorePoint]
    daily_distance: List[DailyDistancePoint]
    daily_time: List[DailyTimePoint]
    suffer_score: List[SufferScorePoint]
    daily_suffer_score: List[DailySufferScorePoint]
    efficiency_trend: List[EfficiencyPoint]
