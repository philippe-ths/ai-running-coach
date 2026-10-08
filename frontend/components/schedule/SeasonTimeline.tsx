"use client";

// #1064: the season as a strip of time.
//
// One bar of phases, widths true to their length, with the runner's dated goals
// pinned above it and a mark for today. Every season is a different length, so
// the strip is as wide as its weeks need (a fixed width per week) and scrolls
// INSIDE its own box: the page never gains a horizontal scrollbar at phone
// width, and a long season is not squeezed into unreadable slivers.
//
// The strip is a picture; the words for a reader are the list under it. Colour
// never carries a phase alone: the legend names every kind drawn, and the list
// states each phase's dates and focus.

import { daysBetween, formatDayMonth, parseIso, todayIso } from "./dates";
import { PHASE_FILL, PHASE_LABEL, PHASE_ORDER, safePhase } from "./palette";
import type { SeasonPhase } from "@/lib/types/season";

const PX_PER_WEEK = 22;
const MIN_WIDTH_PX = 320;

export interface TimelineGoal {
  id: string;
  name: string;
  /** The day the goal stands on: its date, else the start of its window. */
  day: string | null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default function SeasonTimeline({
  phases,
  goals,
}: {
  phases: SeasonPhase[];
  goals: TimelineGoal[];
}) {
  if (phases.length === 0) return null;

  const start = phases.reduce((a, p) => (p.start < a ? p.start : a), phases[0].start);
  const end = phases.reduce((a, p) => (p.end > a ? p.end : a), phases[0].end);
  // Inclusive of the last day, so a one-day race phase still has a width.
  const totalDays = daysBetween(start, end) + 1;
  const width = Math.max(MIN_WIDTH_PX, Math.ceil(totalDays / 7) * PX_PER_WEEK);
  const at = (iso: string) => (daysBetween(start, iso) / totalDays) * 100;

  const today = todayIso();
  const todayInside = today >= start && today <= end;

  // Month ticks along the bottom. The year rides January and the first tick.
  const ticks: { left: number; label: string }[] = [];
  const first = parseIso(start);
  let cursor = new Date(first.getFullYear(), first.getMonth() + (first.getDate() === 1 ? 0 : 1), 1);
  const last = parseIso(end);
  let n = 0;
  while (cursor <= last && n < 60) {
    const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-01`;
    const showYear = cursor.getMonth() === 0 || ticks.length === 0;
    ticks.push({
      left: at(iso),
      label: showYear
        ? `${MONTHS[cursor.getMonth()]} ${String(cursor.getFullYear()).slice(2)}`
        : MONTHS[cursor.getMonth()],
    });
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
    n += 1;
  }

  // Goals the strip can place, sorted so neighbours can be staggered onto
  // alternate lanes: two races a few weeks apart would otherwise print their
  // names on top of each other.
  const pinned = goals
    .filter((g): g is TimelineGoal & { day: string } => !!g.day && g.day >= start && g.day <= end)
    .sort((a, b) => (a.day < b.day ? -1 : 1));
  const LANES = Math.max(1, Math.min(3, pinned.length));

  const kindsDrawn = PHASE_ORDER.filter((k) => phases.some((p) => safePhase(p.kind) === k));

  return (
    <div>
      <div
        className="overflow-x-auto pb-1"
        // The scroller is focusable so a keyboard user can pan it.
        tabIndex={0}
        role="group"
        aria-label="Season timeline, scrolls sideways"
      >
        <div className="relative" style={{ width: `${width}px` }} aria-hidden="true">
          {/* Goal markers, in staggered lanes. */}
          <div className="relative" style={{ height: pinned.length ? `${LANES * 14 + 4}px` : 0 }}>
            {pinned.map((g, i) => (
              <div
                key={g.id}
                className="absolute flex items-start text-rose-700 dark:text-rose-400"
                style={{ left: `${at(g.day)}%`, top: `${(i % LANES) * 14}px`, bottom: 0 }}
              >
                <span className="w-px self-stretch bg-rose-700 dark:bg-rose-400" />
                <span className="whitespace-nowrap pl-1 text-[10px] font-medium leading-[14px]">
                  {g.name}
                </span>
              </div>
            ))}
          </div>

          <div className="relative flex h-4 overflow-hidden rounded-sm">
            {phases.map((p, i) => (
              <span
                key={`${p.start}-${i}`}
                className={`block h-full border-r border-white last:border-r-0 dark:border-gray-800 ${PHASE_FILL[safePhase(p.kind)]}`}
                style={{ width: `${((daysBetween(p.start, p.end) + 1) / totalDays) * 100}%` }}
              />
            ))}
            {todayInside && (
              <span
                className="absolute inset-y-0 w-0.5 bg-gray-900 dark:bg-white"
                style={{ left: `${at(today)}%` }}
              />
            )}
          </div>

          <div className="relative h-4">
            {ticks.map((t) => (
              <span
                key={`${t.left}-${t.label}`}
                className="absolute top-0.5 whitespace-nowrap text-[10px] text-gray-500 dark:text-gray-400"
                style={{ left: `${t.left}%` }}
              >
                {t.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-600 dark:text-gray-300">
        {kindsDrawn.map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-sm ${PHASE_FILL[k]}`} aria-hidden="true" />
            {PHASE_LABEL[k]}
          </li>
        ))}
        {todayInside && (
          <li className="flex items-center gap-1.5">
            <span className="h-3 w-0.5 bg-gray-900 dark:bg-white" aria-hidden="true" />
            Today
          </li>
        )}
      </ul>

      {/* The strip in words, for anyone who cannot read the picture. */}
      <ol className="sr-only">
        {phases.map((p, i) => (
          <li key={`${p.start}-${i}`}>
            {PHASE_LABEL[safePhase(p.kind)]}, {formatDayMonth(p.start)} to{" "}
            {formatDayMonth(p.end)}
            {p.focus ? `: ${p.focus}` : ""}
          </li>
        ))}
        {pinned.map((g) => (
          <li key={g.id}>
            Goal {g.name}, {formatDayMonth(g.day)}
          </li>
        ))}
      </ol>
    </div>
  );
}
