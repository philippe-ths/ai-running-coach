// The week as the strip and the agenda both read it: sessions merged into
// groups, and each group placed on the days it can happen.
//
// A plan often repeats one flexible session ("Brisk walk, any day Tue–Sun",
// seven times). Drawn one by one, seven identical rows bury the sessions that
// differ, so a floating session is merged with every other floating session
// that is the same prescription over the same days. A pinned session is never
// merged: its day is part of what it is.

import type { PlannedSession, SessionIntent, SpacingRuleRead } from "@/lib/types/schedule";
import { addDaysIso, formatDayChip, weekdayShort } from "./dates";

export interface SessionGroup {
  key: string;
  /** Done first, so a group's progress fills from the left. */
  sessions: PlannedSession[];
  first: PlannedSession;
  doneCount: number;
}

function groupKey(s: PlannedSession): string {
  if (s.placement === "pinned") return `pinned:${s.id}`;
  return [
    s.commitment,
    s.intent,
    s.discipline,
    s.title,
    s.detail ?? "",
    s.effective_window_start ?? s.window_start,
    s.effective_window_end ?? s.window_end,
    s.planned_distance_m,
    s.target_duration_s ?? "",
    JSON.stringify(s.structure ?? {}),
  ].join("|");
}

export function groupSessions(sessions: PlannedSession[]): SessionGroup[] {
  const byKey = new Map<string, PlannedSession[]>();
  for (const s of sessions) {
    const key = groupKey(s);
    const list = byKey.get(key) ?? [];
    list.push(s);
    byKey.set(key, list);
  }
  return Array.from(byKey.entries()).map(([key, list]) => {
    const sorted = [...list].sort(
      (a, b) => Number(b.status === "done") - Number(a.status === "done"),
    );
    return {
      key,
      sessions: sorted,
      first: sorted[0],
      doneCount: sorted.filter((s) => s.status === "done").length,
    };
  });
}

/**
 * The days a group's sessions still to do can go on (#1081).
 *
 * The server's `open_days` when it sent them: days the rules still allow, with
 * done sessions holding the day they were done. When it could not settle that
 * (`open_days` null), the effective window is the honest fallback.
 */
export function optionDays(group: SessionGroup): string[] {
  const days = new Set<string>();
  for (const s of group.sessions) {
    if (s.status !== "upcoming") continue;
    if (s.open_days) {
      s.open_days.forEach((d) => days.add(d));
      continue;
    }
    const start = s.effective_window_start ?? s.window_start;
    const end = s.effective_window_end ?? s.window_end;
    for (let d = start; d <= end; d = addDaysIso(d, 1)) days.add(d);
  }
  return Array.from(days).sort();
}

/** "Wed", "Wed or Thu", "Wed, Thu or Fri". */
function dayList(days: string[]): string {
  const names = days.map(weekdayShort);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/** "Tue 20" when pinned, "Tue–Sun" or "Any day" when floating. */
export function placementChip(
  session: PlannedSession,
  weekStart: string,
  weekEnd: string,
): string {
  if (session.placement === "pinned") return formatDayChip(session.window_start);
  const start = session.effective_window_start ?? session.window_start;
  const end = session.effective_window_end ?? session.window_end;
  if (start <= weekStart && end >= weekEnd) return "Any day";
  if (start === end) return formatDayChip(start);
  return `${weekdayShort(start)}–${weekdayShort(end)}`;
}

/**
 * How often, and where, in words: "Once, any one day Tue–Thu" or "3 times,
 * any days this week". A range alone does not say whether it holds one
 * session or several, and that is the first thing a runner asks of it.
 */
export function rangeSentence(group: SessionGroup, weekStart: string, weekEnd: string): string {
  // #1081: while any are still to do, say where what is LEFT can go, from the
  // days the rules still allow, rather than the window the plan first gave.
  const left = group.sessions.filter((s) => s.status === "upcoming").length;
  if (left > 0 && group.first.placement !== "pinned") {
    const days = optionDays(group);
    const all = left === group.sessions.length;
    const howMany = all ? (left === 1 ? "Once" : `${left} times`) : `${left} left`;
    if (!days.length) return `${howMany}, but no day left fits the rules`;
    if (days.length === 7) return `${howMany}, any day this week`;
    if (days.length === 1) return `${howMany}, on ${formatDayChip(days[0])}`;
    return left === 1
      ? `${howMany}, any one of ${dayList(days)}`
      : `${howMany}, on any of ${dayList(days)}`;
  }
  // All done: say when, from the day each one used up.
  if (left === 0 && group.first.placement !== "pinned") {
    const doneDays = Array.from(
      new Set(group.sessions.map((s) => s.done_on).filter((d): d is string => !!d)),
    ).sort();
    if (doneDays.length) {
      return doneDays.length === 1 ? `Done ${formatDayChip(doneDays[0])}` : `Done ${dayList(doneDays)}`;
    }
  }
  const n = group.sessions.length;
  const chip = placementChip(group.first, weekStart, weekEnd);
  // A window narrowed to a single day ("Fri 9") is a day, not a range.
  if (/\d/.test(chip)) return n === 1 ? `Once, on ${chip}` : `${n} times, on ${chip}`;
  const where = chip === "Any day" ? "this week" : chip;
  if (n === 1) return chip === "Any day" ? "Once, any day this week" : `Once, any one day ${where}`;
  return `${n} times, any days ${where}`;
}

/**
 * The week's rules that name this session's intent, in the server's own words.
 *
 * Nothing is evaluated here: the statement is the server's rendering of the
 * predicate it enforces (#844), and this only decides which statements to put
 * beside which session. "At most N a day" names no intent, so it rides only on
 * a repeated session, the one place it changes what the runner can do.
 */
export function rulesFor(
  intent: SessionIntent,
  count: number,
  rules: SpacingRuleRead[],
): string[] {
  return rules
    .filter((r) =>
      r.kind === "max_sessions_per_day"
        ? count > 1
        : [r.intent, r.before_intent, r.target_intent, r.intent_a, r.intent_b].includes(intent),
    )
    .map((r) => r.statement);
}
