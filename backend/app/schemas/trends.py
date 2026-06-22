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


class PeriodDistancePoint(BaseModel):
    """One coarse-granularity bucket of distance (#432: 2-week / month bars).

    ``period_start`` is the bucket's first local day (a fortnight-aligned Monday
    or the first of the month); the values are the sum over that bucket.
    """
    period_start: date
    total_distance_m: int
    activity_count: int


class PeriodTimePoint(BaseModel):
    """One coarse-granularity bucket of moving time (#432)."""
    period_start: date
    total_moving_time_s: int
    activity_count: int


class PeriodSufferScorePoint(BaseModel):
    """One coarse-granularity bucket of accumulated load (#432)."""
    period_start: date
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


class PeriodZoneLoadPoint(BaseModel):
    """One coarse-granularity bucket of 3-zone load (#432: 2-week / month bars)."""
    period_start: date
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
    # #432: coarser bar granularities (2-week / month) rolled up server-side so
    # the frontend can offer a granularity control appropriate to the range.
    biweekly_distance: List[PeriodDistancePoint]
    monthly_distance: List[PeriodDistancePoint]
    biweekly_time: List[PeriodTimePoint]
    monthly_time: List[PeriodTimePoint]
    biweekly_suffer_score: List[PeriodSufferScorePoint]
    monthly_suffer_score: List[PeriodSufferScorePoint]
    biweekly_zone_load: List[PeriodZoneLoadPoint]
    monthly_zone_load: List[PeriodZoneLoadPoint]


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


class VolumeMetricVsNorm(BaseModel):
    """#400: one metric's current-window value vs the runner's norm for that window.

    `current_all` is holistic (every logged activity); `current_runs` is the
    runs-only figure alongside. `norm`/`norm_recent` are the runner's typical output
    over a FULL period of the framing's length (per-day rate over a 12-week / 4-week
    baseline before the window, scaled to the full period — #436), so a calendar
    period-to-date total is compared against a typical full period rather than a
    pro-rated slice. `direction` carries a deadband.
    """
    metric: str  # sessions | distance_m | moving_time_s | effort_score
    current_all: float
    current_runs: float
    norm: Optional[float] = None
    norm_recent: Optional[float] = None
    pct_vs_norm: Optional[float] = None
    direction: str          # up | in_line | down | no_norm
    direction_recent: str   # up | in_line | down | no_norm


class VolumeFraming(BaseModel):
    """#400: one framing of the vs-norm read for the selected range.

    `framing` is "rolling" (the trailing `window_days`) or "calendar" (the current
    calendar period — week/month/quarter/half/year — for the range). `label` is the
    display label ("30-day rolling", "This month"). `days_elapsed` is the days
    counted (= window_days for rolling; the elapsed days of a partial calendar
    period). `complete` is whether the calendar period is finished.
    """
    framing: str        # rolling | calendar
    label: str
    window_days: int
    days_elapsed: int
    complete: bool
    # The actual current-window date range (inclusive), e.g. Jun 1 .. Jun 20.
    period_start: date
    period_end: date
    # The actual baseline date range the norm was computed from (None when history
    # is too thin to establish a norm). Scales with the term — see baseline_label.
    baseline_start: Optional[date] = None
    baseline_end: Optional[date] = None
    metrics: List[VolumeMetricVsNorm]


class VolumeReport(BaseModel):
    """#400: the Trends-page frequency-/volume-vs-norm report for a selected range.

    Two framings (rolling + calendar period) scaled to the range, each metric
    compared to the runner's norm for that window. `has_baseline` is False when
    history is too thin to call a norm (every direction is then `no_norm`).
    `baseline_label` describes the term-scaled baseline lookback (e.g. "the last 12
    weeks" for 7D, "the last 6 months" for 30D)."""
    range: str          # 7D | 30D | 3M | 6M | 1Y
    rolling: VolumeFraming
    calendar: VolumeFraming
    has_baseline: bool
    baseline_label: str
