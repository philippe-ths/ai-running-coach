// #830: the seven-day strip, drawn as a timeline.
//
// Every session is a BAR across the days it can happen: a pinned session is one
// day wide, a floating one spans its window. That puts the whole week on the
// strip, where it used to show only pinned days and park every floating
// session in a separate "still to place" band of dots. Repeated floating
// sessions are one bar ("Brisk walk ×7"), matching the agenda below.
//
// Intent is the bar's colour, as everywhere on the schedule. A narrow bar
// carries the discipline letter, or a run's km; a wide one has the title. A
// suggestion is drawn as an outline, the same dashed language as its card.

import type { ReactNode } from "react";
import type { LoggedActivity, PlannedSession } from "@/lib/types/schedule";
import {
  DISCIPLINE_LABEL,
  DISCIPLINE_LETTER,
  INTENT_FILL,
  INTENT_LABEL,
  INTENT_TEXT,
  intentPip,
  safeDiscipline,
  safeIntent,
} from "./palette";
import { dayOfMonth, formatDayChip, weekDays, weekdayInitial } from "./dates";
import { daySpan, groupSessions, type SessionGroup } from "./agenda";

interface Bar {
  group: SessionGroup;
  start: number;
  end: number;
  lane: number;
}

function barTitle(group: SessionGroup): string {
  const s = group.first;
  const intent = INTENT_LABEL[safeIntent(s.intent)];
  const discipline = DISCIPLINE_LABEL[safeDiscipline(s.discipline)].toLowerCase();
  const n = group.sessions.length;
  const count = n > 1 ? ` ×${n}, ${group.doneCount} done` : s.status === "done" ? ", done" : "";
  return `${s.title}: ${intent} ${discipline}${count}`;
}

/**
 * A one-day bar has room for a few characters. A run carries no letter (it is
 * the default discipline), so it shows its planned km instead: the number a
 * runner most wants at a glance. Anything else keeps its discipline letter.
 */
function narrowLabel(s: PlannedSession): string {
  const discipline = safeDiscipline(s.discipline);
  if (discipline === "run" && s.planned_distance_m > 0) {
    return (s.planned_distance_m / 1000).toFixed(s.planned_distance_m < 10000 ? 1 : 0);
  }
  return DISCIPLINE_LETTER[discipline];
}

/** Greedy interval packing: each bar takes the first lane free on its days. */
function layoutBars(groups: SessionGroup[], weekStart: string): Bar[] {
  const spans = groups
    .map((group) => ({ group, ...daySpan(group.first, weekStart) }))
    .sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const laneEnds: number[] = [];
  return spans.map((span) => {
    let lane = laneEnds.findIndex((end) => end < span.start);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(span.end);
    } else {
      laneEnds[lane] = span.end;
    }
    return { ...span, lane };
  });
}

/**
 * A SET DAY is a solid bar: the session is that day. A RANGE is an outlined,
 * tinted track: the session goes on any day inside it, and the dots inside say
 * how many sessions it holds, one dot each, filled once done. Rest is an
 * absence, so it is only a dashed outline with nothing in it.
 */
function barClass(group: SessionGroup): string {
  const s = group.first;
  const intent = safeIntent(s.intent);
  if (intent === "rest") return intentPip("rest");
  if (s.commitment === "suggested") {
    return `border border-dashed border-current bg-transparent ${INTENT_TEXT[intent]}`;
  }
  if (s.placement !== "pinned") {
    return `border-[1.5px] border-current ${INTENT_TEXT[intent]}`;
  }
  return `${intentPip(intent)} ${s.status === "done" ? "" : "opacity-70"}`;
}

function SessionDots({ group }: { group: SessionGroup }) {
  return (
    <span className="relative flex shrink-0 items-center gap-0.5">
      {group.sessions.map((s) => (
        <span
          key={s.id}
          className={`h-1.5 w-1.5 rounded-full border border-current ${
            s.status === "done" ? "bg-current" : ""
          }`}
        />
      ))}
    </span>
  );
}

export default function DayStrip({
  weekStart,
  sessions,
  logged,
  today,
  header,
}: {
  weekStart: string;
  sessions: PlannedSession[];
  logged: LoggedActivity[];
  today: string;
  /** The week navigator, so changing week is right where the week is read. */
  header?: ReactNode;
}) {
  const days = weekDays(weekStart);
  const groups = groupSessions(sessions.filter((s) => s.status !== "dismissed"));
  const bars = layoutBars(groups, weekStart);
  const laneCount = bars.reduce((max, b) => Math.max(max, b.lane + 1), 0);
  const todayIndex = days.indexOf(today);

  const loggedByDay = new Map<string, number>();
  for (const a of logged) {
    loggedByDay.set(a.local_date, (loggedByDay.get(a.local_date) ?? 0) + 1);
  }

  // The text alternative: one sentence per bar, then what was logged.
  const summary = [
    ...bars.map((b) => {
      const when =
        b.start === b.end
          ? formatDayChip(days[b.start])
          : `${formatDayChip(days[b.start])} to ${formatDayChip(days[b.end])}`;
      return `${when}: ${barTitle(b.group)}`;
    }),
    ...days
      .filter((d) => loggedByDay.get(d))
      .map((d) => `${formatDayChip(d)}: ${loggedByDay.get(d)} logged`),
  ].join(". ");

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:p-4">
      {header}
      <h2 className="sr-only">The week, day by day</h2>
      <p className="sr-only">{summary || "Nothing planned or logged this week yet."}</p>

      <div className="relative" aria-hidden="true">
        {todayIndex >= 0 && (
          <div
            className="absolute inset-y-0 rounded-md bg-gray-100 dark:bg-gray-700/60"
            style={{ left: `${(todayIndex / 7) * 100}%`, width: `${100 / 7}%` }}
          />
        )}

        <ol className="relative grid grid-cols-7">
          {days.map((day, i) => (
            <li key={day} className="flex flex-col items-center gap-0.5 pb-2 pt-1.5">
              <span
                className={`text-[10px] font-medium uppercase ${
                  i === todayIndex
                    ? "text-gray-900 dark:text-gray-100"
                    : "text-gray-400 dark:text-gray-500"
                }`}
              >
                {weekdayInitial(day)}
              </span>
              <span
                className={`font-mono text-xs tabular-nums ${
                  i === todayIndex
                    ? "font-semibold text-gray-900 dark:text-gray-50"
                    : "text-gray-600 dark:text-gray-300"
                }`}
              >
                {dayOfMonth(day)}
              </span>
              <span
                className={`h-0.5 w-4 rounded-full ${
                  loggedByDay.get(day) ? "bg-gray-400 dark:bg-gray-500" : "bg-transparent"
                }`}
              />
            </li>
          ))}
        </ol>

        {laneCount > 0 && (
          <div
            className="relative grid grid-cols-7 gap-y-1 pb-2"
            style={{ gridTemplateRows: `repeat(${laneCount}, 1.25rem)` }}
          >
            {bars.map((b) => {
              const wide = b.end > b.start;
              const s = b.group.first;
              const range = s.placement !== "pinned";
              const isRest = safeIntent(s.intent) === "rest";
              return (
                <div
                  key={b.group.key}
                  title={barTitle(b.group)}
                  style={{ gridColumn: `${b.start + 1} / ${b.end + 2}`, gridRow: b.lane + 1 }}
                  className={`relative mx-0.5 flex min-w-0 items-center overflow-hidden rounded-full px-1.5 text-[10px] font-semibold leading-none ${
                    wide ? "justify-between gap-1" : "justify-center"
                  } ${barClass(b.group)}`}
                >
                  {range && s.commitment === "committed" && (
                    <span aria-hidden="true" className="absolute inset-0 bg-current opacity-20" />
                  )}
                  {isRest ? null : range ? (
                    <>
                      {wide && <span className="relative truncate">{s.title}</span>}
                      <SessionDots group={b.group} />
                    </>
                  ) : (
                    <span className="truncate font-mono tabular-nums">{narrowLabel(s)}</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-gray-500 dark:text-gray-400">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2.5 w-5 rounded-full bg-gray-400 dark:bg-gray-500" />
          Set day
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="relative flex h-2.5 w-8 items-center justify-end overflow-hidden rounded-full border border-current px-0.5"
          >
            <span className="h-1 w-1 rounded-full bg-current" />
          </span>
          Any day in range, one dot per session
        </span>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
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
