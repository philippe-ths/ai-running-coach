// #1064: the season, the coach's read of every goal together. Mirrors backend
// app/schemas/season.py.
//
// Nothing here is the runner's own words except `goals` (their goals, as
// entered). Everything under `plan` is the coach's OPINION, and the event text
// in it is sourced from the web: render it as plain text only.

import type { Discipline, GoalRace } from "./schedule";

export type GoalKind = "challenge" | "race" | "finish" | "completion" | "someday";
export type PhaseKind = "base" | "build" | "sharpen" | "taper" | "race" | "recover";
export type ChallengeMetric = "zone_time_s" | "time_s" | "distance_m" | "sessions";
export type SeasonStatus = "drafting" | "active" | "superseded" | "failed";

/** At least `at_least` of `metric` in every week for `weeks` straight weeks,
 *  in the metric's own unit (seconds, metres, or a count). */
export interface ChallengeRule {
  metric: ChallengeMetric;
  // Empty means every activity counts.
  disciplines: Discipline[];
  min_zone: number | null;
  at_least: number;
  weeks: number;
  start: string;
}

export interface SuggestedEvent {
  name: string;
  date: string | null;
  window_start: string | null;
  window_end: string | null;
  distance_m: number | null;
  url: string;
  why: string;
}

export interface GoalView {
  goal_id: string;
  kind: GoalKind;
  success: string;
  date: string | null;
  window_start: string | null;
  window_end: string | null;
  approach: string;
  events: SuggestedEvent[];
  challenge: ChallengeRule | null;
}

export interface SeasonPhase {
  kind: PhaseKind;
  start: string;
  end: string;
  goal_id: string | null;
  focus: string;
  weekly_hours: number | null;
  run_km: number | null;
  long_run_km: number | null;
}

export interface SeasonPlan {
  summary: string;
  goals: GoalView[];
  phases: SeasonPhase[];
}

export interface ChallengeWeek {
  week_start: string;
  // 1-based: "week 4 of 10".
  index: number;
  threshold: number;
  planned: number | null;
  actual: number | null;
  // null while the week is not over.
  met: boolean | null;
}

export interface ChallengeStatus {
  goal_id: string;
  name: string;
  rule: ChallengeRule;
  weeks: ChallengeWeek[];
  // Consecutive completed weeks met, counting from the start.
  streak: number;
}

/** GET /api/schedule/season. `status` is null when there has never been one. */
export interface SeasonRead {
  id: string | null;
  status: SeasonStatus | null;
  generated_at: string | null;
  model_id: string | null;
  plan: SeasonPlan | null;
  goals: GoalRace[];
  challenges: ChallengeStatus[];
  // True when the runner's goals changed since this season was written.
  stale: boolean;
  shortfalls: string[];
  message: string | null;
}
