/**
 * Barrel re-exports for all frontend types.
 *
 * Import from here: `import { Activity } from '@/lib/types'`
 */

export type { DerivedMetric, ActivityStream, CheckIn } from "./types/metrics";
export type { Activity, TrainingLoad } from "./types/activity";
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
  PeriodDistancePoint,
  PeriodTimePoint,
  PeriodSufferScorePoint,
  PeriodZoneLoadPoint,
  TrendsSummary,
  TrendsData,
  TrendsRange,
  TrendsGranularity,
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
export type { ChatMessage, ToolTraceEntry } from "./types/chat";
export type {
  VoiceDials,
  VoiceAxisInfo,
  VoicePresetInfo,
  VoiceCatalog,
  VoiceConfig,
  DialKey,
} from "./types/voice";
export type {
  StanceSelection,
  StanceSchoolInfo,
  StanceAxisInfo,
  StanceCatalog,
  StanceConfig,
  EmphasisKey,
} from "./types/stance";
export type {
  MaterialKind,
  MaterialStatus,
  DistilledMaterial,
  UserMaterial,
} from "./types/material";

