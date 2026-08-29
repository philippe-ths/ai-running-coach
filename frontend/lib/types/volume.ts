// #400 frequency-/volume-vs-norm report (mirrors backend VolumeReport).

export type VolumeDirection = "up" | "in_line" | "down" | "no_norm";

export type VolumeMetricName =
  | "sessions"
  | "distance_m"
  | "moving_time_s"
  | "effort_score"
  | "zone_2_plus_minutes";

export interface VolumeMetricVsNorm {
  metric: VolumeMetricName;
  current_all: number;
  current_runs: number;
  norm: number | null;
  norm_recent: number | null;
  pct_vs_norm: number | null;
  direction: VolumeDirection;
  direction_recent: VolumeDirection;
}

export interface VolumeFraming {
  framing: "rolling" | "calendar";
  label: string;
  window_days: number;
  days_elapsed: number;
  complete: boolean;
  period_start: string; // ISO date
  period_end: string; // ISO date
  baseline_start: string | null; // ISO date
  baseline_end: string | null; // ISO date
  metrics: VolumeMetricVsNorm[];
}

export interface VolumeReport {
  range: string;
  rolling: VolumeFraming;
  calendar: VolumeFraming;
  has_baseline: boolean;
  baseline_label: string;
}
