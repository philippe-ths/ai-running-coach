// #400 frequency-/volume-vs-norm signal (mirrors backend TrainingVolumeContext).

export type VolumeDirection = "up" | "in_line" | "down" | "no_norm";

export type VolumeMetricName =
  | "sessions"
  | "distance_m"
  | "moving_time_s"
  | "effort_score";

export interface VolumeMetricComparison {
  metric: VolumeMetricName;
  current_all: number;
  current_runs: number;
  norm_weekly: number | null;
  norm_weekly_recent: number | null;
  pct_vs_norm: number | null;
  direction: VolumeDirection;
  direction_recent: VolumeDirection;
}

export interface VolumeWindow {
  window: "rolling_7d" | "calendar_week";
  days_elapsed: number;
  complete: boolean;
  metrics: VolumeMetricComparison[];
}

export interface TrainingVolume {
  rolling_7d: VolumeWindow;
  calendar_week: VolumeWindow;
  baseline_weeks: number;
  baseline_weeks_recent: number;
  has_baseline: boolean;
}
