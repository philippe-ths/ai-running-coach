"""
Pydantic schemas for the /api/trends endpoint.
"""

from datetime import date
from typing import List, Optional
from uuid import UUID

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


class ZoneLoadWeekPoint(BaseModel):
    """One week of 3-zone load data (Easy / Moderate / Hard minutes)."""
    week_start: date
    easy_min: float
    moderate_min: float
    hard_min: float


class DailyZoneLoadPoint(BaseModel):
    """One day of 3-zone load data (Easy / Moderate / Hard minutes)."""
    date: date
    easy_min: float
    moderate_min: float
    hard_min: float


class TrendsSummary(BaseModel):
    total_distance_m: int
    total_moving_time_s: int
    activity_count: int
    total_suffer_score: float
    # Period aggregates backing the graph-card deltas (#385). Efficiency is an
    # average (a rate, not a sum) and is None when no activity in the window has
    # usable HR/distance. Zone minutes are split per HR band so the Zone-Load
    # card can show an Easy / Moderate / Hard delta.
    avg_efficiency_mps_per_bpm: Optional[float] = None
    zone_easy_minutes: float = 0.0
    zone_moderate_minutes: float = 0.0
    zone_hard_minutes: float = 0.0


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
    weekly_zone_load: List[ZoneLoadWeekPoint]
    daily_zone_load: List[DailyZoneLoadPoint]


class WeeklyStatsSummary(BaseModel):
    """Totals for a rolling 7-day window used by the dashboard summary cards."""
    total_distance_m: int
    total_moving_time_s: int
    activity_count: int
    total_load: float
    hard_days: int


class WeeklyStatsResponse(BaseModel):
    """Current rolling 7-day window plus the prior 7 days for comparison (#246, #248)."""
    summary: WeeklyStatsSummary
    previous_summary: WeeklyStatsSummary


class LoadActivityPoint(BaseModel):
    """One activity's contribution to a week's training load (#209)."""
    id: UUID
    name: str
    date: date
    effort_score: float
    headline: Optional[str] = None


class LoadWeek(BaseModel):
    """One calendar week (Monday start) of training load (#209)."""
    week_start: date
    score: float
    daily: List[float]  # 7 values, Monday..Sunday
    target_min: Optional[float] = None  # optimal band from the trailing 4-week avg
    target_max: Optional[float] = None
    status: str  # below | optimal | high | no_baseline
    activities: List[LoadActivityPoint]


class LoadResponse(BaseModel):
    weeks: List[LoadWeek]  # chronological; last entry is the current week
