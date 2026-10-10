// #830, #1081: the seven-day strip.
//
// Each day is a column listing what is on it. A session DONE sits on the day it
// was done, filled. A session with a SET DAY sits on that day, outlined. A
// session that floats is drawn as a dashed OPTION on every day it can still go,
// which the server works out with the same rule search the plan is held to
// (`open_days`): a day the rules close, a day already used up by a done
// session, and a day already gone all drop out, so the options shrink as the
// week goes. A column shows three chips at most and then "+N".
//
// Repeated floating sessions are one option per day, not one per session: three
// easy runs over the week are one "easy run could go here" per day, and the
// pool line under the strip says how many are still to place.
//
// Intent is the chip's colour, as everywhere on the schedule. A run shows its
// planned km; anything else its discipline letter.

import type { ReactNode } from "react";
import type { LoggedActivity, PlannedSession, SpacingRuleRead } from "@/lib/types/schedule";
import {
  DISCIPLINE_LABEL,
  DISCIPLINE_LETTER,
  INTENT_FILL,
  INTENT_LABEL,
  INTENT_TEXT,
  safeDiscipline,
  safeIntent,
} from "./palette";
import { dayOfMonth, formatDayChip, weekDays, weekdayInitial } from "./dates";
import { groupSessions, optionDays } from "./agenda";

const MAX_CHIPS = 3;

type ChipKind = "done" | "set" | "option" | "offer";

interface Chip {
  key: string;
  session: PlannedSession;
  kind: ChipKind;
}

/** What fits on a chip: a run's km, otherwise the discipline's letter. */
function chipLabel(s: PlannedSession): string {
  const discipline = safeDiscipline(s.discipline);
  if (discipline === "run") {
    if (s.planned_distance_m > 0) {
      return (s.planned_distance_m / 1000).toFixed(s.planned_distance_m < 10000 ? 1 : 0);
    }
    if (s.target_duration_s) return `${Math.round(s.target_duration_s / 60)}'`;
    return "Run";
  }
  return DISCIPLINE_LETTER[discipline];
}

function chipTitle(chip: Chip): string {
  const s = chip.session;
  const what = `${s.title} (${INTENT_LABEL[safeIntent(s.intent)]} ${DISCIPLINE_LABEL[
    safeDiscipline(s.discipline)
  ].toLowerCase()})`;
  if (chip.kind === "done") return `${what}, done`;
  if (chip.kind === "option") return `${what} could go here`;
  if (chip.kind === "offer") return `${what}, a suggestion`;
  return what;
}

function chipClass(chip: Chip): string {
  const intent = safeIntent(chip.session.intent);
  if (intent === "rest") {
    return "border-2 border-dashed border-stone-400 dark:border-stone-500";
  }
  if (chip.kind === "done") return `${INTENT_FILL[intent]} text-white`;
  if (chip.kind === "set") return `border-2 border-current ${INTENT_TEXT[intent]}`;
  if (chip.kind === "offer") {
    return `border border-dashed border-current opacity-60 ${INTENT_TEXT[intent]}`;
  }
  return `border-[1.5px] border-dashed border-current ${INTENT_TEXT[intent]}`;
}

/** Every chip, by the ISO day it belongs on. */
function chipsByDay(sessions: PlannedSession[], weekStart: string, weekEnd: string) {
  const byDay = new Map<string, Chip[]>();
  const add = (day: string | null | undefined, chip: Chip) => {
    if (!day || day < weekStart || day > weekEnd) return;
    const list = byDay.get(day) ?? [];
    list.push(chip);
    byDay.set(day, list);
  };

  for (const s of sessions) {
    if (s.status === "dismissed" || s.status === "missed") continue;
    if (s.status === "done") {
      add(s.done_on ?? s.window_start, { key: `${s.id}-done`, session: s, kind: "done" });
    } else if (s.placement === "pinned") {
      add(s.window_start, {
        key: s.id,
        session: s,
        kind: s.commitment === "suggested" ? "offer" : "set",
      });
    }
  }

  // Floating sessions still to do: one option per group per open day.
  const floating = sessions.filter(
    (s) => s.status === "upcoming" && s.placement !== "pinned" && s.commitment === "committed",
  );
  for (const group of groupSessions(floating)) {
    for (const day of optionDays(group)) {
      add(day, { key: `${group.key}-${day}`, session: group.first, kind: "option" });
    }
  }

  const order: Record<ChipKind, number> = { done: 0, set: 1, option: 2, offer: 3 };
  byDay.forEach((list) => list.sort((a, b) => order[a.kind] - order[b.kind]));
  return byDay;
}

/** "5 to place · 4 days left · any order", or "one each" when the limit is one. */
function poolLine(
  sessions: PlannedSession[],
  rules: SpacingRuleRead[],
  today: string,
  days: string[],
): string | null {
  const floating = sessions.filter(
    (s) =>
      s.status === "upcoming" &&
      s.placement !== "pinned" &&
      s.commitment === "committed" &&
      safeIntent(s.intent) !== "rest",
  );
  if (!floating.length) return null;
  const open = new Set<string>();
  for (const group of groupSessions(floating)) optionDays(group).forEach((d) => open.add(d));
  const daysLeft = days.filter((d) => d >= today && open.has(d)).length;
  const caps = rules
    .filter((r) => r.kind === "max_sessions_per_day" && r.count)
    .map((r) => r.count as number);
  const oneEach = caps.length > 0 && Math.min(...caps) === 1;
  const n = floating.length;
  return `${n} to place · ${daysLeft} day${daysLeft === 1 ? "" : "s"} left · ${
    oneEach ? "one each, any order" : "any order"
  }`;
}

export default function DayStrip({
  weekStart,
  sessions,
  logged,
  rules,
  today,
  header,
}: {
  weekStart: string;
  sessions: PlannedSession[];
  logged: LoggedActivity[];
  rules: SpacingRuleRead[];
  today: string;
  /** The week navigator, so changing week is right where the week is read. */
  header?: ReactNode;
}) {
  const days = weekDays(weekStart);
  const byDay = chipsByDay(sessions, days[0], days[6]);
  const todayIndex = days.indexOf(today);
  const pool = poolLine(sessions, rules, today, days);

  const loggedByDay = new Map<string, number>();
  for (const a of logged) {
    loggedByDay.set(a.local_date, (loggedByDay.get(a.local_date) ?? 0) + 1);
  }

  // The text alternative: each day's chips in words.
  const summary = days
    .map((day) => {
      const chips = byDay.get(day) ?? [];
      if (!chips.length) return null;
      return `${formatDayChip(day)}: ${chips.map(chipTitle).join(", ")}`;
    })
    .filter(Boolean)
    .join(". ");

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:p-4">
      {header}
      <h2 className="sr-only">The week, day by day</h2>
      <p className="sr-only">
        {[summary || "Nothing planned this week yet.", pool].filter(Boolean).join(". ")}
      </p>

      <ol className="grid grid-cols-7" aria-hidden="true">
        {days.map((day, i) => {
          const chips = byDay.get(day) ?? [];
          const shown = chips.slice(0, MAX_CHIPS);
          const hidden = chips.length - shown.length;
          const isToday = i === todayIndex;
          return (
            <li
              key={day}
              className={`flex min-h-[7.5rem] flex-col items-center gap-1 px-0.5 pb-2 pt-1.5 ${
                isToday ? "rounded-md bg-gray-100 dark:bg-gray-700/60" : ""
              } ${i > 0 ? "border-l border-dashed border-gray-100 dark:border-gray-700/70" : ""}`}
            >
              <span
                className={`text-[10px] font-medium uppercase ${
                  isToday ? "text-gray-900 dark:text-gray-100" : "text-gray-400 dark:text-gray-500"
                }`}
              >
                {weekdayInitial(day)}
              </span>
              <span
                className={`font-mono text-xs tabular-nums ${
                  isToday
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
              {shown.map((chip) => (
                <span
                  key={chip.key}
                  title={chipTitle(chip)}
                  className={`flex h-5 w-full max-w-[2.75rem] items-center justify-center rounded-full font-mono text-[10px] font-semibold leading-none tabular-nums ${chipClass(
                    chip,
                  )}`}
                >
                  {safeIntent(chip.session.intent) === "rest" ? "" : chipLabel(chip.session)}
                  {chip.kind === "done" && " ✓"}
                </span>
              ))}
              {hidden > 0 && (
                <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400">
                  +{hidden}
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {pool && (
        <p className="mt-1 border-t border-dashed border-gray-200 pt-2 text-center text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300">
          {pool}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-gray-500 dark:text-gray-400">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2.5 w-5 rounded-full bg-gray-400 dark:bg-gray-500" />
          Done
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2.5 w-5 rounded-full border-2 border-gray-400 dark:border-gray-500" />
          Set day
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-5 rounded-full border-[1.5px] border-dashed border-gray-400 dark:border-gray-500"
          />
          Could go here
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
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
