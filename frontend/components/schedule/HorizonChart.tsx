// #830: the horizon — three months of training as load per week.
//
// VERTICAL by design. Twelve weeks as horizontal bars running DOWN the page,
// chosen over a twelve-column chart because at phone width twelve columns get
// ~28px each and force 8px labels. As rows every week gets the full width, the
// ramp reads down the right-hand edge, and a wide screen simply lengthens the
// bars — one layout, no breakpoint where the reading changes.
//
// Bar LENGTH is the week's load as a true proportion of the peak week. True
// proportions are the whole point: segment widths that overflow their track make
// every bar look maxed out and the ramp disappears. So the bar's length carries
// the peak proportion and the segments inside it only ever divide THAT length —
// they are laid out with flex-grow against a zero basis, which cannot overflow
// however the shares round.
//
// TWO BANDS, ONE LENGTH, because they are the same week's load seen two ways: a
// thick band split by discipline over a thin band split by intent. Nothing is
// normalised away, so the ramp reads identically down either edge.
//
// `planned` vs sketched is a TICK, not a fade. Fading sketched rows washes out
// ten of twelve rows and costs the reader the ramp to say something a 8px mark
// says better.
//
// The chart itself is aria-hidden and its text alternative is the table twin
// below it, which carries every number the bars encode plus the phases and the
// races. Screen readers always reach it; sighted readers toggle it open.

import type { ReactNode } from "react";
import type { GoalRace, ScheduleHorizon } from "@/lib/types/schedule";
import { formatDateLabel } from "@/lib/format";
import { addDaysIso } from "./dates";
import {
  DISCIPLINE_FILL,
  DISCIPLINE_LABEL,
  DISCIPLINE_ORDER,
  INTENT_FILL,
  INTENT_LABEL,
  INTENT_ORDER,
  safeDiscipline,
  safeIntent,
} from "./palette";

export type Segmentation = "both" | "intent" | "discipline";

interface Segment {
  key: string;
  label: string;
  fill: string;
  share: number;
}

// Draw order is FIXED by the palette's declared order, never sorted by share.
// A stack re-sorted per week makes its segments jump sideways row to row, which
// destroys the one thing a vertical horizon is for: tracking a band down the
// page.
function buildSegments(
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

function disciplineSegments(mix: Record<string, number>): Segment[] {
  return buildSegments(
    mix,
    DISCIPLINE_ORDER,
    (k) => DISCIPLINE_LABEL[safeDiscipline(k)],
    (k) => DISCIPLINE_FILL[safeDiscipline(k)],
  );
}

function intentSegments(mix: Record<string, number>): Segment[] {
  return buildSegments(
    mix,
    INTENT_ORDER,
    (k) => INTENT_LABEL[safeIntent(k)],
    (k) => INTENT_FILL[safeIntent(k)],
  );
}

function pct(share: number): number {
  return Math.round(share * 100);
}

/** Running km, exact. A week with no running distance reads as an absence. */
function km(metres: number | null): string {
  if (metres == null || metres <= 0) return "—";
  return (metres / 1000).toFixed(1);
}

function describeMix(segments: Segment[]): string {
  if (segments.length === 0) return "no mix";
  return segments.map((s) => `${s.label} ${pct(s.share)}%`).join(", ");
}

/** One band of the bar: the segments, separated by 2px of the row's surface. */
function Band({
  segments,
  height,
  surface,
}: {
  segments: Segment[];
  height: string;
  surface: string;
}) {
  return (
    <div className={`flex ${height} gap-[2px] overflow-hidden rounded-r-[3px] ${surface}`}>
      {segments.map((s) => (
        <div
          key={s.key}
          className={s.fill}
          // flex-grow against a zero basis: the shares divide the bar's own
          // length and cannot overflow it, however they round.
          style={{ flexGrow: s.share, flexBasis: 0, minWidth: 0 }}
        />
      ))}
    </div>
  );
}

const GRID_STOPS = [25, 50, 75];

const ROW_GRID =
  "grid grid-cols-[3.25rem_0.75rem_minmax(0,1fr)_3rem] items-center gap-x-2";

export default function HorizonChart({
  horizon,
  segmentation,
  showTable,
}: {
  horizon: ScheduleHorizon;
  segmentation: Segmentation;
  showTable: boolean;
}) {
  const peak = horizon.peak_effort_score ?? 0;
  const showDiscipline = segmentation !== "intent";
  const showIntent = segmentation !== "discipline";
  const both = segmentation === "both";

  // A race lands in the week whose seven days contain it. Weeks are contiguous
  // and the API only returns races inside the span, so a race that matches no
  // week is a mismatch worth not silently dropping — it falls to the tail.
  const racesByWeek = new Map<string, GoalRace[]>();
  const placed = new Set<string>();
  for (const race of horizon.races) {
    const week = horizon.weeks.find(
      (w) => race.race_date >= w.week_start && race.race_date <= addDaysIso(w.week_start, 6),
    );
    if (!week) continue;
    placed.add(race.id);
    const list = racesByWeek.get(week.week_start) ?? [];
    list.push(race);
    racesByWeek.set(week.week_start, list);
  }
  const unplacedRaces = horizon.races.filter((r) => !placed.has(r.id));

  const rows: ReactNode[] = [];
  let lastPhase: string | null = null;

  for (const week of horizon.weeks) {
    const disc = disciplineSegments(week.discipline_mix);
    const intent = intentSegments(week.intent_mix);
    const load = week.effort_score ?? 0;
    const length = peak > 0 && load > 0 ? Math.min(100, (load / peak) * 100) : 0;

    if (week.phase && week.phase !== lastPhase) {
      rows.push(
        <div
          key={`phase-${week.week_start}`}
          className="mt-3 flex items-center gap-2 pb-1 pt-2 first:mt-0 first:pt-0"
        >
          <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            {week.phase}
          </span>
          <span className="h-px flex-1 bg-gray-200 dark:bg-gray-700" aria-hidden="true" />
        </div>,
      );
      lastPhase = week.phase;
    }

    // The row's own surface, which is also what shows through the 2px gaps
    // between segments. The current week is tinted, so the gaps have to be
    // tinted with it or every gap would read as a white seam.
    const surface = week.is_current
      ? "bg-blue-50 dark:bg-blue-950/50"
      : "bg-white dark:bg-gray-800";

    const summary = [
      `Week of ${formatDateLabel(week.week_start)}`,
      load > 0 ? `${Math.round(load)} load` : "nothing planned",
      week.running_distance_m ? `${km(week.running_distance_m)} km running` : null,
      disc.length ? `discipline: ${describeMix(disc)}` : null,
      intent.length ? `intent: ${describeMix(intent)}` : null,
    ]
      .filter(Boolean)
      .join(" · ");

    rows.push(
      <div
        key={week.week_start}
        className={`${ROW_GRID} rounded-sm py-1 ${
          week.is_current ? "bg-blue-50 dark:bg-blue-950/50" : ""
        }`}
      >
        <span
          className={`font-mono text-[11px] tabular-nums ${
            week.is_current
              ? "font-semibold text-blue-800 dark:text-blue-300"
              : "text-gray-500 dark:text-gray-400"
          }`}
        >
          {formatDateLabel(week.week_start)}
        </span>

        {/* Solid = real sessions, hollow = shape only. A tick rather than a
            fade: fading the sketched weeks washes out most of the chart. */}
        <span className="flex justify-center">
          {week.planned ? (
            <span className="h-2 w-2 rounded-full bg-gray-600 dark:bg-gray-300" />
          ) : (
            <span className="h-2 w-2 rounded-full border border-dashed border-gray-400 dark:border-gray-500" />
          )}
        </span>

        <div className={`relative ${both ? "h-5" : "h-3.5"} ${surface}`} title={summary}>
          {GRID_STOPS.map((stop) => (
            <span
              key={stop}
              className="absolute inset-y-0 w-px bg-gray-200 dark:bg-gray-700"
              style={{ left: `${stop}%` }}
            />
          ))}
          {length > 0 && (
            <div
              className={`absolute inset-y-0 left-0 flex flex-col justify-center gap-[2px] ${surface}`}
              style={{ width: `${length}%` }}
            >
              {showDiscipline && (
                <Band
                  segments={disc}
                  height={both ? "h-3" : "h-3.5"}
                  surface={surface}
                />
              )}
              {showIntent && (
                <Band
                  segments={intent}
                  height={both ? "h-1.5" : "h-3.5"}
                  surface={surface}
                />
              )}
            </div>
          )}
        </div>

        <span
          className={`text-right font-mono text-[11px] tabular-nums ${
            week.is_current
              ? "font-semibold text-blue-800 dark:text-blue-300"
              : "text-gray-600 dark:text-gray-300"
          }`}
        >
          {km(week.running_distance_m)}
        </span>
      </div>,
    );

    for (const race of racesByWeek.get(week.week_start) ?? []) {
      rows.push(
        <div key={race.id} className={`${ROW_GRID} py-0.5`}>
          <span className="font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {formatDateLabel(race.race_date)}
          </span>
          <span className="flex justify-center">
            <span className="h-2 w-0.5 bg-rose-700 dark:bg-rose-400" />
          </span>
          <span className="flex items-center gap-2 truncate text-[11px] font-medium text-rose-700 dark:text-rose-400">
            <span className="h-px w-3 shrink-0 bg-rose-700 dark:bg-rose-400" />
            <span className="truncate">{race.name}</span>
          </span>
          <span className="text-right font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {(race.distance_m / 1000).toFixed(0)}k
          </span>
        </div>,
      );
    }
  }

  // The legend covers exactly the series actually on the chart. Identity never
  // rests on colour alone: every swatch is named here and again in the table.
  const legendDisciplines = new Map<string, Segment>();
  const legendIntents = new Map<string, Segment>();
  for (const week of horizon.weeks) {
    for (const s of disciplineSegments(week.discipline_mix)) legendDisciplines.set(s.key, s);
    for (const s of intentSegments(week.intent_mix)) legendIntents.set(s.key, s);
  }

  const hasAnyLoad = peak > 0;

  return (
    <div>
      <div aria-hidden="true">
        <div className="space-y-0.5">{rows}</div>

        {/* The scale. Bars are true proportions of the peak week, so the reader
            is owed the number the proportion is of. */}
        <div className={`${ROW_GRID} mt-2 border-t border-gray-100 pt-1.5 dark:border-gray-700`}>
          <span />
          <span />
          <span className="flex justify-between text-[10px] text-gray-400 dark:text-gray-500">
            <span className="font-mono tabular-nums">0</span>
            <span>
              {hasAnyLoad ? (
                <>
                  <span className="font-mono tabular-nums">{Math.round(peak)}</span> load
                </>
              ) : (
                "no load"
              )}
            </span>
          </span>
          <span />
        </div>

        <div className="mt-3 space-y-1.5">
          {showDiscipline && legendDisciplines.size > 0 && (
            <LegendRow
              title={both ? "Discipline" : "Discipline · thick band"}
              items={Array.from(legendDisciplines.values())}
            />
          )}
          {showIntent && legendIntents.size > 0 && (
            <LegendRow
              title={both ? "Intent" : "Intent · thick band"}
              items={Array.from(legendIntents.values())}
            />
          )}
          <p className="pt-1 text-[11px] text-gray-400 dark:text-gray-500">
            Bar length is the week&rsquo;s load against your biggest week. Solid tick =
            real sessions, hollow tick = shape only. The number on the right is
            running km, so a long bar beside a small number is a week you cross-train.
          </p>
        </div>
      </div>

      <HorizonTable
        horizon={horizon}
        peak={peak}
        unplacedRaces={unplacedRaces}
        className={showTable ? "mt-6" : "sr-only"}
      />
    </div>
  );
}

function LegendRow({ title, items }: { title: string; items: Segment[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
        {title}
      </span>
      {items.map((s) => (
        <span
          key={s.key}
          className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400"
        >
          <span className={`h-2 w-2 shrink-0 rounded-sm ${s.fill}`} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/**
 * The chart's text alternative, and the only place every value is written out.
 *
 * Always in the DOM: hidden it is `sr-only`, so a screen-reader user reaches the
 * whole ramp, the mixes, the phases and the races without seeing the bars; shown
 * it is the same table for everyone else. The bars are aria-hidden precisely so
 * this is read once rather than twice.
 */
function HorizonTable({
  horizon,
  peak,
  unplacedRaces,
  className,
}: {
  horizon: ScheduleHorizon;
  peak: number;
  unplacedRaces: GoalRace[];
  className: string;
}) {
  const cell = "px-2 py-1.5 align-top";
  const head =
    "px-2 py-1.5 text-left font-semibold text-gray-600 dark:text-gray-300";

  return (
    <div className={className}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-[11px] text-gray-600 dark:text-gray-300">
          <caption className="pb-2 text-left text-[11px] text-gray-500 dark:text-gray-400">
            Every week in the horizon, in words. Load is shown as a share of the
            biggest week ({peak > 0 ? `${Math.round(peak)} load` : "no load planned"}),
            which is what the bar lengths encode.
          </caption>
          <thead>
            <tr className="border-b border-gray-200 dark:border-gray-700">
              <th scope="col" className={head}>
                Week of
              </th>
              <th scope="col" className={head}>
                Phase
              </th>
              <th scope="col" className={head}>
                Plan
              </th>
              <th scope="col" className={head}>
                Load
              </th>
              <th scope="col" className={head}>
                Running
              </th>
              <th scope="col" className={head}>
                Discipline
              </th>
              <th scope="col" className={head}>
                Intent
              </th>
            </tr>
          </thead>
          <tbody>
            {horizon.weeks.map((week) => {
              const load = week.effort_score ?? 0;
              const share = peak > 0 && load > 0 ? Math.round((load / peak) * 100) : 0;
              return (
                <tr
                  key={week.week_start}
                  className="border-b border-gray-100 last:border-0 dark:border-gray-700/60"
                >
                  <th
                    scope="row"
                    className={`${cell} whitespace-nowrap text-left font-medium text-gray-700 dark:text-gray-200`}
                  >
                    {formatDateLabel(week.week_start)}
                    {week.is_current && " (this week)"}
                  </th>
                  <td className={cell}>{week.phase ?? "—"}</td>
                  <td className={cell}>{week.planned ? "Sessions" : "Shape only"}</td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {load > 0 ? `${share}% of peak` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {week.running_distance_m ? `${km(week.running_distance_m)} km` : "—"}
                  </td>
                  <td className={cell}>{describeMix(disciplineSegments(week.discipline_mix))}</td>
                  <td className={cell}>{describeMix(intentSegments(week.intent_mix))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {horizon.races.length > 0 && (
        <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
          Races in this window:{" "}
          {horizon.races
            .map(
              (r) =>
                `${r.name} on ${formatDateLabel(r.race_date)} (${(r.distance_m / 1000).toFixed(0)} km)`,
            )
            .join("; ")}
          .
          {unplacedRaces.length > 0 && " Some fall outside the weeks shown."}
        </p>
      )}
    </div>
  );
}
