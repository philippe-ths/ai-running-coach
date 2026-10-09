// The week as the strip and the agenda both read it: sessions merged into
// groups, and each group placed on the days it can happen.
//
// A plan often repeats one flexible session ("Brisk walk, any day Tue–Sun",
// seven times). Drawn one by one, seven identical rows bury the sessions that
// differ, so a floating session is merged with every other floating session
// that is the same prescription over the same days. A pinned session is never
// merged: its day is part of what it is.

import type { PlannedSession } from "@/lib/types/schedule";
import { daysBetween, formatDayChip, weekdayShort } from "./dates";

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
 * The columns (0 = first day of the week) a session can fall on.
 *
 * A floating session uses its effective window, the days still open to it.
 * That window can sit wholly outside the week being viewed (a past week, where
 * "today onward" has moved on), and then the stored window is the honest span.
 */
export function daySpan(
  s: PlannedSession,
  weekStart: string,
): { start: number; end: number } {
  const clamp = (from: string, to: string) => ({
    start: Math.max(0, daysBetween(weekStart, from)),
    end: Math.min(6, daysBetween(weekStart, to)),
  });
  if (s.placement === "pinned") return clamp(s.window_start, s.window_start);
  const effective = clamp(
    s.effective_window_start ?? s.window_start,
    s.effective_window_end ?? s.window_end,
  );
  if (effective.start <= effective.end) return effective;
  const stored = clamp(s.window_start, s.window_end);
  return stored.start <= stored.end ? stored : { start: 0, end: 6 };
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
