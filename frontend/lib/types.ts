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
  SufferScorePoint,
  DailySufferScorePoint,
  WeeklySufferScorePoint,
  EfficiencyPoint,
  ZoneLoadWeekPoint,
  DailyZoneLoadPoint,
  TrendsSummary,
  TrendsData,
  TrendsRange,
} from "./types/trends";
export type {
  CoachTakeaway,
  CoachReport,
  CoachReportContent,
  CoachNextStep,
  CoachRisk,
  CoachQuestion,
  CoachReportMeta,
} from "./types/coach";

