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

import type { VolumeDirection, VolumeMetricVsNorm } from "./volume";

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

// #844: the runner-facing rule TEXT the API actually returns. `statement` is
// derived server-side from `kind` + arguments — the only text guaranteed to
// match what the rule's predicate enforces. `label` is the coach's own prose,
// carried alongside as a subordinate note; a runner following `label` used to
// be able to trigger the very rule it described (a live label promised "or an
// easy walk" against a predicate that forbade exactly that).
export interface SpacingRuleRead extends SpacingRule {
  statement: string;
}

export interface RuleViolation {
  kind: RuleKind;
  label: string;
  statement: string;
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
  // How far this session goes, decided server-side by
  // `services/schedule/planned_distance.py` — the same value the week's
  // headline is summed from (#887). 0 means the session states no distance;
  // a duration is never converted into one.
  planned_distance_m: number;
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

// The runs-only vs-norm read, which is what the free-mode headline is actually
// made of. `norm` below stays the all-activity Trends view; this one measures
// the same quantity the big running-km number does, so the gauge under it is
// not a second unit stacked on the first.
//
// `typical_weekly_distance_m` is the WHOLE week's typical, while
// `current_distance_m` is the week so far — the server pro-rates the norm by
// days elapsed before computing `pct_vs_norm`, so the percentage is fair on a
// Tuesday and the caption can still quote a full typical week.
export interface RunningVsNorm {
  typical_weekly_distance_m: number;
  current_distance_m: number;
  pct_vs_norm: number;
  direction: VolumeDirection;
  deadband_pct: number;
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
  rules: SpacingRuleRead[];
  violations: RuleViolation[];
  // The runner's own typical week, from the same builder Trends uses. Present
  // only for the current week, where "as of today" means anything.
  norm: VolumeMetricVsNorm[] | null;
  // The runs-only read for the free-mode gauge. Present only for the current
  // week, and only once there is enough history to say what typical is.
  running_norm: RunningVsNorm | null;
}

export interface DraftStatus {
  status: "drafting" | "active" | "superseded" | "failed" | null;
  plan_id: string | null;
  generated_at: string | null;
  message: string;
}

// #857: the plan the runner was training to before this one.
//
// Always an object: "you have no earlier plan" is an answer with its own
// sentence, not a 404. `restorable` is the SERVER's verdict: the refusal has a
// reason the runner is owed (a plan whose horizon has passed would leave them
// with nothing planned), and a client re-deriving it from the dates would
// eventually derive it differently from the endpoint that enforces it.
export interface PreviousPlan {
  plan_id: string | null;
  // When it stopped being current, and when its thinking was written. Two
  // different facts: a plan replaced this morning can be one drafted in June.
  superseded_at: string | null;
  generated_at: string | null;
  horizon_end: string | null;
  // Sessions of that plan that still lie ahead: whether going back to it would
  // actually give the runner anything.
  sessions_ahead: number;
  restorable: boolean;
  message: string;
}

// A is the race the block is built towards; B and C are races run along the
// way. The read keeps `priority` as a plain string because the stored column is
// one — only the WRITE is closed to the three the API accepts.
export type RacePriority = "A" | "B" | "C";

export interface GoalRace {
  id: string;
  name: string;
  race_date: string;
  distance_m: number;
  priority: string;
}

export interface GoalRaceCreate {
  name: string;
  race_date: string;
  distance_m: number;
  priority: RacePriority;
}

// --- the horizon ------------------------------------------------------------
//
// The block's SHAPE rather than its sessions: concrete for about three weeks,
// sketched beyond. `planned` is derived server-side from what is actually
// there, so it can never claim a week holds sessions it does not.
//
// A week the plan says nothing about still arrives, with a null load and empty
// mixes, so the horizon stays a continuous run of weeks and a gap reads as a
// gap rather than as a shorter block.

// The four states a week can be in (#842). `planned`/`sketched` mirror the
// `planned` boolean below; `empty` is a genuine interior GAP the plan's own
// span covers but says nothing about, while `beyond_plan` is a week past the
// plan's last covered week — the plan never had a chance to sketch it, so it
// must not read as "shape only, not written yet" the way a real sketched week
// does. With no plan at all, every week is `empty`; there is nothing to be
// beyond.
export type HorizonCoverage = "planned" | "sketched" | "empty" | "beyond_plan";

export interface HorizonWeek {
  week_start: string;
  phase: string | null;
  // True when the week holds committed sessions; false when it is shape only.
  // Always equal to `coverage === "planned"`.
  planned: boolean;
  coverage: HorizonCoverage;
  is_current: boolean;
  running_distance_m: number | null;
  effort_score: number | null;
  // discipline/intent -> share of the week's load, 0..1. Shares, not absolutes,
  // so a mix can never contradict the total it is a mix of. An all-zero week
  // yields an empty map rather than a fake even split.
  discipline_mix: Record<string, number>;
  intent_mix: Record<string, number>;
}

export interface ScheduleHorizon {
  weeks: HorizonWeek[];
  races: GoalRace[];
  has_plan: boolean;
  // The largest weekly load in the window. Bar lengths are true proportions of
  // it — null (or zero) means there is nothing to scale against and every week
  // draws empty.
  peak_effort_score: number | null;
}
