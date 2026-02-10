/**
 * Barrel re-exports for all frontend types.
 *
 * Import from here: `import { Activity } from '@/lib/types'`
 */

export type { DerivedMetric, ActivityStream, CheckIn } from "./types/metrics";
export type { Activity } from "./types/activity";
export type { UserProfile } from "./types/profile";
export type {
  WeeklyDistancePoint,
  WeeklyTimePoint,
  DailyDistancePoint,
  DailyTimePoint,
  PaceTrendPoint,
  TrendsData,
  TrendsRange,
} from "./types/trends";

