import { DerivedMetric, ActivityStream, CheckIn } from "./metrics";

export interface Split {
  split: number;
  split_type: 'distance' | 'time';
  distance?: number;
  elapsed_time: number;
  pace?: number;
  speed?: number;
  avg_hr?: number;
  avg_grade?: number;
  avg_cadence?: number;
  avg_watts?: number;
  elev_gain?: number;
}

export interface Lap {
  lap: number;
  name?: string | null;
  distance_m?: number | null;
  elapsed_time_s?: number | null;
  moving_time_s?: number | null;
  avg_speed_mps?: number | null;
  avg_hr?: number | null;
  max_hr?: number | null;
  avg_cadence?: number | null;
}

// P3 readiness (ADR 0016): the runner's current-condition read as of this activity.
export interface TrainingLoad {
  fitness: number;       // chronic load (zone-minutes, ~42d EWMA)
  fatigue: number;       // acute load (zone-minutes, ~7d EWMA)
  form: number;          // fitness − fatigue
  ramp_rate: number;     // fitness change over the last 7 days
  condition: string;     // fresh | balanced | fatigued | overreaching | building_baseline
  trend: string;         // building | steady | detraining
  ramp_aggressive: boolean;
  warming_up: boolean;
  sample_count: number;
}

export interface Activity {
  id: string;
  name: string;
  start_date: string;
  distance_m: number;
  moving_time_s: number;
  avg_hr?: number;
  max_hr?: number;
  elev_gain_m: number;
  avg_cadence?: number;
  user_intent?: string;
  metrics?: DerivedMetric;
  raw_summary?: any;
  streams?: ActivityStream[];
  check_in?: CheckIn;
  splits?: Split[];
  laps?: Lap[];
  training_load?: TrainingLoad | null;
}
