export interface StopLocation {
  start_time: number;
  duration_s: number;
  location?: [number, number];
  distance_m?: number;
}

export interface StopsAnalysis {
  total_stopped_time_s: number;
  stopped_count: number;
  longest_stop_s: number;
  stops: StopLocation[];
}

export interface EfficiencyAnalysis {
  average: number;
  best_sustained: number;
  curve: number[];
  unit: string;
}

export interface DerivedMetric {
  // Orthogonal classification axes (ADR 0007) + the derived headline.
  headline?: string | null;
  effort?: string | null;
  duration_class?: string | null;
  structure?: string | null;
  is_hilly?: boolean | null;
  is_race?: boolean | null;
  effort_score: number;
  flags: string[];
  confidence: string;
  confidence_reasons: string[];
  pace_variability?: number;
  hr_drift?: number;
  time_in_zones?: Record<string, number>;
  /** "strava" when zones are binned from the runner's own Strava HR bounds;
   *  null/undefined means the %-of-max-HR fallback was used (#301). */
  hr_zones_source?: string | null;
  stops_analysis?: StopsAnalysis;
  efficiency_analysis?: EfficiencyAnalysis;
}

export interface ActivityStream {
  stream_type: string;
  data: any[];
}

export interface CheckIn {
  rpe: number;
  pain_score: number;
  pain_location?: string;
  sleep_quality?: number;
  notes?: string;
}
