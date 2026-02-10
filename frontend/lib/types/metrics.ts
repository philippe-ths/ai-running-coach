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
  activity_class: string;
  effort_score: number;
  flags: string[];
  confidence: string;
  confidence_reasons: string[];
  pace_variability?: number;
  hr_drift?: number;
  time_in_zones?: Record<string, number>;
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
