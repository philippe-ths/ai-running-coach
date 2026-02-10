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

export interface PaceTrendPoint {
  date: string;
  pace_sec_per_km: number;
  type: string;
}

export interface TrendsData {
  range: string;
  weekly_distance: WeeklyDistancePoint[];
  weekly_time: WeeklyTimePoint[];
  pace_trend: PaceTrendPoint[];
}

export type TrendsRange = "7D" | "30D" | "3M" | "6M" | "1Y" | "ALL";
