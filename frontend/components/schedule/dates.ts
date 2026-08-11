// #830: ISO-date helpers for the week view.
//
// Every date the schedule API returns is a calendar day ("2026-08-10") with no
// time and no zone. Parsing one through `new Date(iso)` makes it midnight UTC,
// which renders as the PREVIOUS day for any viewer behind UTC — the bug
// `formatDateLabel` already avoids by splitting the string. These helpers keep
// that discipline: local-component construction in, string out.

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_INITIAL = ["S", "M", "T", "W", "T", "F", "S"];

export function parseIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function toIso(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function addDaysIso(iso: string, days: number): string {
  const d = parseIso(iso);
  d.setDate(d.getDate() + days);
  return toIso(d);
}

export function todayIso(): string {
  return toIso(new Date());
}

/** "Mon" */
export function weekdayShort(iso: string): string {
  return WEEKDAY_SHORT[parseIso(iso).getDay()];
}

/** "M" — the day strip's column head. */
export function weekdayInitial(iso: string): string {
  return WEEKDAY_INITIAL[parseIso(iso).getDay()];
}

/** 10 */
export function dayOfMonth(iso: string): number {
  return parseIso(iso).getDate();
}

/** "Mon 10" — a pinned session's placement chip. */
export function formatDayChip(iso: string): string {
  return `${weekdayShort(iso)} ${dayOfMonth(iso)}`;
}

/** The seven ISO days of the week beginning `weekStart`. */
export function weekDays(weekStart: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDaysIso(weekStart, i));
}
