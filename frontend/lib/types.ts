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
  WeeklyStatsSummary,
  WeeklyStatsData,
  LoadActivityPoint,
  LoadStatus,
  LoadWeek,
  LoadData,
} from "./types/trends";
export type {
  EvidenceRef,
  CoachTakeaway,
  CoachReport,
  CoachReportContent,
  CoachMessageReport,
  CoachReportBody,
  CoachMessageQuestion,
  TappableOption,
  CoachNextStep,
  CoachRisk,
  CoachQuestion,
  CoachReportMeta,
} from "./types/coach";
export { isMessageReport, isOpenerOnly } from "./types/coach";
export type { ChatMessage } from "./types/chat";

