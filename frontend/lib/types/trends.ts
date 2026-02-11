export interface WeeklyDistancePoint {
  week_start: string;
  total_distance_m: number;
  activity_count: number;
}

export interface WeeklyTimePoint {
  week_start: string;
  total_moving_time_s: number;
  activity_count: number;
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
}

export interface EfficiencyPoint {
  date: string;
  efficiency_mps_per_bpm: number;
  type: string;
}

export interface TrendsSummary {
  total_distance_m: number;
  total_moving_time_s: number;
  activity_count: number;
  total_suffer_score: number;
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
}

export type TrendsRange = "7D" | "30D" | "3M" | "6M" | "1Y" | "ALL";
