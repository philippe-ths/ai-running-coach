export interface WeeklyDistancePoint {
  week_start: string;
  total_distance_m: number;
  activity_count: number;
  // #566: edge-bucket coverage. out_of_period_days > 0 marks a partial week
  // straddling the period boundary; out_of_period_distance_m is the value of
  // its days outside the window, stacked as a faded segment so the bar shows
  // the whole week.
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_distance_m?: number;
}

export interface WeeklyTimePoint {
  week_start: string;
  total_moving_time_s: number;
  activity_count: number;
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_moving_time_s?: number;
}

export interface DailyDistancePoint {
  date: string;
  total_distance_m: number;
  activity_count: number;
}

export interface DailyTimePoint {
  date: string;
  total_moving_time_s: number;
  activity_count: number;
}

export interface SufferScorePoint {
  date: string;
  effort_score: number;
  type: string;
}

export interface DailySufferScorePoint {
  date: string;
  effort_score: number;
}

export interface WeeklySufferScorePoint {
  week_start: string;
  effort_score: number;
  // #566: edge-bucket coverage + out-of-window load; see WeeklyDistancePoint.
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_effort_score?: number;
}

export interface EfficiencyPoint {
  date: string;
  efficiency_mps_per_bpm: number;
  type: string;
}

export interface ZoneLoadWeekPoint {
  week_start: string;
  easy_min: number;
  moderate_min: number;
  hard_min: number;
}

export interface DailyZoneLoadPoint {
  date: string;
  easy_min: number;
  moderate_min: number;
  hard_min: number;
}

// #432: coarse-granularity (2-week / month) bucket points, keyed by the bucket's
// first local day. One shape per metric, shared by the biweekly and monthly series.
export interface PeriodDistancePoint {
  period_start: string;
  total_distance_m: number;
  activity_count: number;
  // #566: edge-bucket coverage; see WeeklyDistancePoint.
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_distance_m?: number;
}

export interface PeriodTimePoint {
  period_start: string;
  total_moving_time_s: number;
  activity_count: number;
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_moving_time_s?: number;
}

export interface PeriodSufferScorePoint {
  period_start: string;
  effort_score: number;
  in_period_days?: number | null;
  out_of_period_days?: number;
  out_of_period_effort_score?: number;
}

export interface PeriodZoneLoadPoint {
  period_start: string;
  easy_min: number;
  moderate_min: number;
  hard_min: number;
}

export interface TrendsSummary {
  total_distance_m: number;
  total_moving_time_s: number;
  activity_count: number;
  total_suffer_score: number;
  // Period aggregates backing the graph-card deltas (#385). Efficiency is null
  // when no activity in the window has usable HR/distance. Zone minutes are
  // split per HR band for the Zone-Load card's Easy / Moderate / Hard deltas.
  avg_efficiency_mps_per_bpm?: number | null;
  zone_easy_minutes?: number;
  zone_moderate_minutes?: number;
  zone_hard_minutes?: number;
}

export interface TrendsData {
  range: string;
  summary: TrendsSummary;
  previous_summary?: TrendsSummary | null;
  weekly_distance: WeeklyDistancePoint[];
  weekly_time: WeeklyTimePoint[];
  weekly_suffer_score: WeeklySufferScorePoint[];
  daily_distance: DailyDistancePoint[];
  daily_time: DailyTimePoint[];
  suffer_score: SufferScorePoint[];
  daily_suffer_score: DailySufferScorePoint[];
  efficiency_trend: EfficiencyPoint[];
  weekly_zone_load: ZoneLoadWeekPoint[];
  daily_zone_load: DailyZoneLoadPoint[];
  // #432: coarser bar granularities rolled up server-side.
  biweekly_distance: PeriodDistancePoint[];
  monthly_distance: PeriodDistancePoint[];
  biweekly_time: PeriodTimePoint[];
  monthly_time: PeriodTimePoint[];
  biweekly_suffer_score: PeriodSufferScorePoint[];
  monthly_suffer_score: PeriodSufferScorePoint[];
  biweekly_zone_load: PeriodZoneLoadPoint[];
  monthly_zone_load: PeriodZoneLoadPoint[];
}

export type TrendsRange = "7D" | "30D" | "3M" | "6M" | "1Y" | "ALL";

// #432: bar granularity (bucket size) the runner can choose per range.
export type TrendsGranularity = "day" | "week" | "2week" | "month";

export interface WeeklyStatsSummary {
  total_distance_m: number;
  total_moving_time_s: number;
  activity_count: number;
  total_load: number;
  hard_days: number;
}

export interface WeeklyStatsData {
  summary: WeeklyStatsSummary;
  previous_summary: WeeklyStatsSummary;
}

export interface LoadActivityPoint {
  id: string;
  name: string;
  date: string;
  effort_score: number;
  headline?: string | null;
}

export type LoadStatus = 'below' | 'optimal' | 'high' | 'no_baseline';

export interface LoadWeek {
  week_start: string;
  score: number;
  daily: number[]; // 7 values, Monday..Sunday
  target_min?: number | null;
  target_max?: number | null;
  status: LoadStatus;
  activities: LoadActivityPoint[];
}

export interface LoadData {
  weeks: LoadWeek[];
}
