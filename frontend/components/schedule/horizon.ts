import type {
  GoalRace,
  HorizonChallenge,
  HorizonWeek,
} from "@/lib/types/schedule";
import { formatDateLabel } from "@/lib/format";
import { addDaysIso } from "./dates";
import {
  formatHeld,
  formatNeeded,
  metricLabel,
  metricUnit,
  planMeetsThreshold,
  weekFigure,
} from "./challenge";
import {
  ACTIVITY_FILL,
  DISCIPLINE_LABEL,
  DISCIPLINE_ORDER,
  INTENT_LABEL,
  INTENT_ORDER,
  safeDiscipline,
  safeIntent,
} from "./palette";

export interface Segment {
  key: string;
  label: string;
  fill: string;
  share: number;
}

// Draw order is FIXED by the palette's declared order, never sorted by share.
// A stack re-sorted per week makes its segments jump sideways row to row, which
// destroys the one thing a vertical horizon is for: tracking a band down the
// page.
export function buildSegments(
  mix: Record<string, number>,
  order: readonly string[],
  label: (key: string) => string,
  fill: (key: string) => string,
): Segment[] {
  const seen = new Set<string>();
  const out: Segment[] = [];
  for (const key of order) {
    const share = mix[key];
    if (typeof share === "number" && share > 0) {
      seen.add(key);
      out.push({ key, label: label(key), fill: fill(key), share });
    }
  }
  // A key the plan invented that is not in the vocabulary still has to draw, or
  // the bands would silently under-report the week they are a mix of. It keeps
  // its OWN key so React never collides two unknowns onto one degraded slot.
  for (const [key, share] of Object.entries(mix)) {
    if (!seen.has(key) && share > 0) {
      out.push({ key, label: label(key), fill: fill(key), share });
    }
  }
  return out;
}

export function activitySegments(mix: Record<string, number>): Segment[] {
  return buildSegments(
    mix,
    DISCIPLINE_ORDER,
    (k) => DISCIPLINE_LABEL[safeDiscipline(k)],
    (k) => ACTIVITY_FILL[safeDiscipline(k)],
  );
}

export function kindSegments(mix: Record<string, number>): Segment[] {
  return buildSegments(
    mix,
    INTENT_ORDER,
    (k) => INTENT_LABEL[safeIntent(k)],
    () => "",
  );
}

export function pct(share: number): number {
  return Math.round(share * 100);
}

/** Km, exact. A week with no such distance reads as an absence. */
export function km(metres: number | null | undefined): string {
  if (metres == null || metres <= 0) return "—";
  return (metres / 1000).toFixed(1);
}

/** Hours, one decimal. A dash when not every session states a time. */
export function hours(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return "—";
  return (seconds / 3600).toFixed(1);
}

/** "14.0 km", or "90 min" for a long run given only as a time (#985). */
export function longRun(week: HorizonWeek): string | null {
  if (week.long_run_distance_m) return `${km(week.long_run_distance_m)} km`;
  if (week.long_run_duration_s) return `${Math.round(week.long_run_duration_s / 60)} min`;
  return null;
}

/** Km for the row: whole numbers, since the row is a glance, not a log. */
export function kmShort(metres: number | null | undefined): string {
  return String(Math.round((metres ?? 0) / 1000));
}

/** "run 30 · walk 15 km", leaving out any part that is null or zero. */
export function distanceLine(week: HorizonWeek): string | null {
  const parts: string[] = [];
  if ((week.running_distance_m ?? 0) >= 500) parts.push(`run ${kmShort(week.running_distance_m)}`);
  if ((week.walking_distance_m ?? 0) >= 500) parts.push(`walk ${kmShort(week.walking_distance_m)}`);
  return parts.length ? `${parts.join(" · ")} km` : null;
}

export function describeMix(segments: Segment[]): string {
  if (segments.length === 0) return "no mix";
  return segments.map((s) => `${s.label} ${pct(s.share)}%`).join(", ");
}

// The Plan column's short word, one per coverage (#842) — the numbers table's
// four-valued reading of the same state the row's tick and detail panel show.
export const PLAN_COLUMN_TEXT: Record<HorizonWeek["coverage"], string> = {
  planned: "Sessions",
  sketched: "Shape only",
  empty: "Nothing planned",
  beyond_plan: "Past the end of the plan",
};

// The week-detail's own sentence, one per coverage (#842). `sketched` keeps
// the literal "Shape only, not written yet" — now true of sketched weeks
// alone, since it used to be printed for `beyond_plan` weeks too, which the
// coach never had a chance to sketch.
export function coverageDetailText(coverage: HorizonWeek["coverage"]): string {
  switch (coverage) {
    case "planned":
      return "Real sessions planned";
    case "sketched":
      return "Shape only, not written yet";
    case "empty":
      return "Nothing planned this week";
    case "beyond_plan":
      return "Past the end of the plan";
  }
}

// --- challenge lines (#1064) -------------------------------------------------

export type ChallengeState = "met" | "missed" | "plan-ok" | "plan-short" | "unplanned";

export interface ChallengeReading {
  state: ChallengeState;
  /** "Z2+ 10.3 / 10 h" */
  figures: string;
  /** The symbol drawn beside the figures; the word carries it for a reader. */
  symbol: string;
  /** "met", "missed", "plan falls short", ... */
  word: string;
  /** "wk 4/10" */
  position: string;
}

export const CHALLENGE_TONE: Record<ChallengeState, string> = {
  met: "text-emerald-700 dark:text-emerald-400",
  missed: "text-red-700 dark:text-red-400",
  "plan-ok": "text-gray-600 dark:text-gray-300",
  "plan-short": "text-amber-700 dark:text-amber-400",
  unplanned: "text-gray-500 dark:text-gray-400",
};

/**
 * One challenge week, read. A past week is judged on what was DONE (`met`); a
 * week still ahead is judged on what the plan HOLDS, and a plan that falls short
 * says so rather than drawing the same tick as one that does not.
 */
export function readChallenge(c: HorizonChallenge, withName: boolean): ChallengeReading {
  const value = weekFigure(c);
  const label = metricLabel(c.metric, c.min_zone);
  const name = withName ? `${c.name}: ` : "";
  const figures =
    value == null
      ? `${name}${label} - / ${formatNeeded(c.metric, c.threshold)} ${metricUnit(c.metric, c.threshold)}`
      : `${name}${label} ${formatHeld(c.metric, value)} / ${formatNeeded(c.metric, c.threshold)} ${metricUnit(c.metric, c.threshold)}`;
  const position = `wk ${c.index}/${c.weeks}`;
  if (c.met === true) return { state: "met", figures, symbol: "\u2713", word: "met", position };
  if (c.met === false) {
    return { state: "missed", figures, symbol: "\u2717", word: "missed", position };
  }
  if (c.planned == null) {
    return { state: "unplanned", figures, symbol: "\u2013", word: "not planned yet", position };
  }
  return planMeetsThreshold(c.metric, c.planned, c.threshold)
    ? { state: "plan-ok", figures, symbol: "\u2713", word: "planned", position }
    : { state: "plan-short", figures, symbol: "\u25B2", word: "plan falls short", position };
}

export function describeChallenges(challenges: HorizonChallenge[]): string {
  const withName = challenges.length > 1;
  return challenges
    .map((c) => {
      const r = readChallenge(c, withName);
      return `challenge ${r.figures}, ${r.word}, week ${c.index} of ${c.weeks}`;
    })
    .join(", ");
}

/** The day a goal stands on in the chart: its exact date, else the start of its window. */
export function goalDay(goal: GoalRace): string | null {
  return goal.race_date ?? goal.window_start;
}

/** The date column's short label: "Nov 8", or "~Oct" for a window (its start). */
export function goalColumnLabel(goal: GoalRace): string {
  if (goal.race_date) return formatDateLabel(goal.race_date);
  return goal.window_start ? `~${formatDateLabel(goal.window_start).split(" ")[0]}` : "";
}

/** Whether a week overlaps a goal's window: the weeks a range goal runs through. */
export function weekInWindow(weekStart: string, goal: GoalRace): boolean {
  if (goal.race_date || !goal.window_start || !goal.window_end) return false;
  return weekStart <= goal.window_end && addDaysIso(weekStart, 6) >= goal.window_start;
}

/** "Nov 8" for an exact date; "~Mar" or "~May–Jun" for an approximate one. */
export function goalDayLabel(goal: GoalRace): string {
  if (goal.race_date) return formatDateLabel(goal.race_date);
  if (!goal.window_start) return "";
  const start = formatDateLabel(goal.window_start).split(" ")[0];
  const end = goal.window_end ? formatDateLabel(goal.window_end).split(" ")[0] : start;
  return start === end ? `~${start}` : `~${start}–${end}`;
}
