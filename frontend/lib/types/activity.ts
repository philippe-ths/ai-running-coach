import { DerivedMetric, ActivityStream, CheckIn } from "./metrics";

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
}
