export interface DerivedMetric {
  activity_class: string;
  effort_score: number;
  flags: string[];
  confidence: string;
  confidence_reasons: string[];
  pace_variability?: number;
  hr_drift?: number;
  time_in_zones?: Record<string, number>;
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
