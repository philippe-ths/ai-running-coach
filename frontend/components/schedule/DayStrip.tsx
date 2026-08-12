// #830: the seven-day strip.
//
// A pinned session's pip sits on its day and carries that day's INTENT as
// colour. Discipline rides inside the pip as a LETTER, never as a second
// colour. A floating session has no day yet, so it is not on the strip at all —
// it waits in the "still to place" band below, which is the honest rendering of
// a session whose day is not decided.

import type { LoggedActivity, PlannedSession } from "@/lib/types/schedule";
import {
  DISCIPLINE_LABEL,
  DISCIPLINE_LETTER,
  INTENT_FILL,
  INTENT_LABEL,
  intentPip,
  safeDiscipline,
  safeIntent,
} from "./palette";
import { dayOfMonth, formatDayChip, weekDays, weekdayInitial } from "./dates";

function pipTitle(s: PlannedSession): string {
  const intent = INTENT_LABEL[safeIntent(s.intent)];
  const discipline = DISCIPLINE_LABEL[safeDiscipline(s.discipline)];
  return `${s.title} — ${intent} ${discipline.toLowerCase()}`;
}

export default function DayStrip({
  weekStart,
  sessions,
  logged,
  today,
}: {
  weekStart: string;
  sessions: PlannedSession[];
  logged: LoggedActivity[];
  today: string;
}) {
  const days = weekDays(weekStart);

  const pinnedByDay = new Map<string, PlannedSession[]>();
  for (const s of sessions) {
    if (s.placement !== "pinned" || s.status === "dismissed") continue;
    const list = pinnedByDay.get(s.window_start) ?? [];
    list.push(s);
    pinnedByDay.set(s.window_start, list);
  }

  const loggedByDay = new Map<string, number>();
  for (const a of logged) {
    loggedByDay.set(a.local_date, (loggedByDay.get(a.local_date) ?? 0) + 1);
  }

  const floating = sessions.filter(
    (s) => s.placement !== "pinned" && s.status === "upcoming" && s.commitment === "committed",
  );

  // The text alternative: the whole strip as one sentence per day that has
  // anything on it.
  const summary = days
    .map((day) => {
      const pinned = pinnedByDay.get(day) ?? [];
      const count = loggedByDay.get(day) ?? 0;
      if (!pinned.length && !count) return null;
      const planned = pinned.map((s) => pipTitle(s)).join(", ");
      const loggedText = count ? `${count} logged` : "";
      return `${formatDayChip(day)}: ${[planned, loggedText].filter(Boolean).join("; ")}`;
    })
    .filter(Boolean)
    .join(". ");

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <h2 className="sr-only">The week, day by day</h2>
      <p className="sr-only">{summary || "Nothing planned or logged this week yet."}</p>

      <ol className="grid grid-cols-7 gap-1" aria-hidden="true">
        {days.map((day) => {
          const pinned = pinnedByDay.get(day) ?? [];
          const loggedCount = loggedByDay.get(day) ?? 0;
          const isToday = day === today;
          return (
            <li
              key={day}
              className={`flex flex-col items-center gap-1 rounded-md py-2 ${
                isToday
                  ? "ring-2 ring-gray-900 dark:ring-gray-100"
                  : "ring-1 ring-transparent"
              }`}
            >
              <span className="text-[10px] font-medium uppercase text-gray-400 dark:text-gray-500">
                {weekdayInitial(day)}
              </span>
              <span className="font-mono text-xs tabular-nums text-gray-600 dark:text-gray-300">
                {dayOfMonth(day)}
              </span>
              <span className="flex min-h-[1.5rem] flex-col items-center gap-1">
                {pinned.map((s) => (
                  <span
                    key={s.id}
                    title={pipTitle(s)}
                    className={`flex h-5 w-5 items-center justify-center rounded-full text-[9px] font-bold leading-none ${intentPip(
                      safeIntent(s.intent),
                    )} ${s.status === "done" ? "" : "opacity-70"}`}
                  >
                    {DISCIPLINE_LETTER[safeDiscipline(s.discipline)]}
                  </span>
                ))}
              </span>
              <span
                className={`h-0.5 w-5 rounded-full ${
                  loggedCount > 0
                    ? "bg-gray-400 dark:bg-gray-500"
                    : "bg-transparent"
                }`}
                title={loggedCount > 0 ? `${loggedCount} logged` : undefined}
              />
            </li>
          );
        })}
      </ol>

      {floating.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-dashed border-gray-300 px-3 py-2 dark:border-gray-600">
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {floating.length} session{floating.length === 1 ? "" : "s"} still to place
          </span>
          <span className="flex items-center gap-1">
            {floating.map((s) => (
              <span
                key={s.id}
                title={pipTitle(s)}
                className={`h-2.5 w-2.5 rounded-full ${
                  safeIntent(s.intent) === "rest"
                    ? "border border-dashed border-stone-400 dark:border-stone-500"
                    : INTENT_FILL[safeIntent(s.intent)]
                }`}
              />
            ))}
          </span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">
        {(Object.keys(INTENT_LABEL) as (keyof typeof INTENT_LABEL)[]).map((intent) => (
          <span
            key={intent}
            className="flex items-center gap-1.5 text-[10px] text-gray-400 dark:text-gray-500"
          >
            <span
              className={`h-2 w-2 rounded-full ${
                intent === "rest"
                  ? "border border-dashed border-stone-400 dark:border-stone-500"
                  : INTENT_FILL[intent]
              }`}
              aria-hidden="true"
            />
            {INTENT_LABEL[intent]}
          </span>
        ))}
      </div>
    </section>
  );
}
