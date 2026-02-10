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


class PaceTrendPoint(BaseModel):
    date: date
    pace_sec_per_km: float
    type: str


class TrendsResponse(BaseModel):
    range: str
    weekly_distance: List[WeeklyDistancePoint]
    weekly_time: List[WeeklyTimePoint]
    pace_trend: List[PaceTrendPoint]
