// #1064: how a challenge's numbers are written, shared by the season card and
// the horizon so the two never say the same week two ways.
//
// Units follow the metric: the two time metrics arrive in seconds and are shown
// as hours, distance arrives in metres and is shown as km, sessions are a count.

import type { ChallengeMetric } from "@/lib/types/season";

type Metric = ChallengeMetric;

// How far under its threshold a PLANNED week may sit and still meet it. A mirror
// of `_TOLERANCE` in backend/app/services/schedule/week_check.py, the check the
// plan was held to: a plan is a prescription, not a stopwatch, so a minute or ten
// metres short is a week that holds. Keep the two in step, or the screen shows a
// triangle ("plan falls short") for a week the backend accepted.
const PLAN_TOLERANCE: Record<Metric, number> = {
  zone_time_s: 60,
  time_s: 60,
  distance_m: 10,
  sessions: 0,
};

/** Whether a planned week meets its threshold, by the backend check's tolerance. */
export function planMeetsThreshold(metric: Metric, planned: number, threshold: number): boolean {
  return planned >= threshold - PLAN_TOLERANCE[metric];
}

/**
 * The figure to show beside a challenge week. A week that is over shows what the
 * runner DID; any other week shows what the PLAN holds, which already counts
 * what has been done so far this week. The measured partial of the current week
 * is never drawn beside a plan mark: it is part of the plan's figure, not a rival.
 */
export function weekFigure(week: {
  met: boolean | null;
  actual: number | null;
  planned: number | null;
}): number | null {
  return week.met != null ? week.actual : week.planned;
}

function scaled(metric: Metric, value: number): number {
  if (metric === "zone_time_s" || metric === "time_s") return value / 3600;
  if (metric === "distance_m") return value / 1000;
  return value;
}

function trim(n: number): string {
  const s = n.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/**
 * What the runner HAS, truncated rather than rounded: a week that fell a hair
 * short of its threshold must never print as "10 / 10" beside a miss mark.
 */
export function formatHeld(metric: Metric, value: number): string {
  if (metric === "sessions") return String(Math.floor(value + 1e-9));
  return trim(Math.floor(scaled(metric, value) * 10 + 1e-9) / 10);
}

/** What the week needs, rounded up for the same reason `formatHeld` truncates. */
export function formatNeeded(metric: Metric, value: number): string {
  if (metric === "sessions") return String(Math.ceil(value - 1e-9));
  return trim(Math.ceil(scaled(metric, value) * 10 - 1e-9) / 10);
}

export function metricUnit(metric: Metric, amount?: number): string {
  if (metric === "zone_time_s" || metric === "time_s") return "h";
  if (metric === "distance_m") return "km";
  return amount === 1 ? "session" : "sessions";
}

/** "Z2+" for a zone metric; the plain metric name otherwise. */
export function metricLabel(metric: Metric, minZone: number | null): string {
  if (metric === "zone_time_s") return minZone ? `Z${minZone}+` : "Zone time";
  if (metric === "time_s") return "Time";
  if (metric === "distance_m") return "Distance";
  return "Sessions";
}
