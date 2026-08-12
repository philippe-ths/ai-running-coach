// #830: the schedule — what the runner is doing next. Mirrors backend
// app/schemas/schedule.py.
//
// Three independent axes describe a planned session: PLACEMENT (where in the
// week it sits), COMMITMENT (agreed to, or merely suggested) and DISCIPLINE
// (which sport). Intent is the coaching read — an easy bike and an easy run are
// the same stimulus — so intent carries the colour and discipline is named
// beside it, never given a competing colour of its own.
//
// Placement is DERIVED server-side from a stored inclusive window; there is no
// mode the runner picks. `effective_window_*` is that window narrowed to today
// onward, so a floating session's days shrink as the week passes while the
// stored window never moves.

import type { VolumeMetricVsNorm } from "./volume";

export type SessionIntent = "rest" | "easy" | "long" | "quality" | "strength";
export type Discipline =
  | "run"
  | "walk"
  | "bike"
  | "strength"
  | "row"
  | "other";
export type Commitment = "committed" | "suggested";
export type Placement = "pinned" | "window" | "week";
export type SessionStatus = "upcoming" | "done" | "missed" | "dismissed";

export type RuleKind =
  | "rest_day_after"
  | "no_intent_day_before"
  | "min_days_between"
  | "preferred_days"
  | "max_sessions_per_day";

export interface SpacingRule {
  kind: RuleKind;
  label: string;
  source: "coach" | "runner";
  intent?: SessionIntent | null;
  before_intent?: SessionIntent | null;
  target_intent?: SessionIntent | null;
  intent_a?: SessionIntent | null;
  intent_b?: SessionIntent | null;
  days?: number | null;
  weekdays?: number[] | null;
  count?: number | null;
}

export interface RuleViolation {
  kind: RuleKind;
  label: string;
  detail: string;
}

export interface PlannedSession {
  id: string;
  window_start: string;
  window_end: string;
  placement: Placement;
  effective_window_start: string | null;
  effective_window_end: string | null;
  has_narrowed: boolean;
  intent: SessionIntent;
  discipline: Discipline;
  commitment: Commitment;
  status: SessionStatus;
  title: string;
  detail: string | null;
  target_distance_m: number | null;
  target_duration_s: number | null;
  target_effort_score: number | null;
  structure: Record<string, number> | null;
  completed_at: string | null;
  completed_activity_id: string | null;
  completion_source: string | null;
  dismissed_at: string | null;
}

export interface LoggedActivity {
  activity_id: string | null;
  local_date: string;
  activity_type: string;
  discipline: Discipline;
  distance_m: number;
  moving_time_s: number;
  effort_score: number;
}

// Sized by effort_score, the only unit that sums across a gym session and a
// turbo ride; km cannot, because strength and an indoor bike have no distance.
export interface DisciplineLoad {
  discipline: Discipline;
  planned_effort_score: number;
  logged_effort_score: number;
  planned_sessions: number;
  logged_sessions: number;
}

export interface WeekHeadline {
  // null in free mode — there is no target to report.
  planned_running_distance_m: number | null;
  logged_running_distance_m: number;
  planned_sessions: number;
  done_sessions: number;
}

export interface ScheduleWeek {
  week_start: string;
  week_end: string;
  is_current_week: boolean;
  // Whether an ACTIVE plan row exists. NOT the free-mode signal: a plan holding
  // only suggestions is still a free week. Render free mode off
  // `headline.planned_sessions === 0`.
  has_plan: boolean;
  plan_id: string | null;
  headline: WeekHeadline;
  sessions: PlannedSession[];
  logged: LoggedActivity[];
  by_discipline: DisciplineLoad[];
  rules: SpacingRule[];
  violations: RuleViolation[];
  // The runner's own typical week, from the same builder Trends uses. Present
  // only for the current week, where "as of today" means anything.
  norm: VolumeMetricVsNorm[] | null;
}

export interface DraftStatus {
  status: "drafting" | "active" | "superseded" | "failed" | null;
  plan_id: string | null;
  generated_at: string | null;
  message: string;
}

export interface GoalRace {
  id: string;
  name: string;
  race_date: string;
  distance_m: number;
  priority: string;
}
