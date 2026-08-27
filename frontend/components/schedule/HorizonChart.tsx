"use client";

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
// COVERAGE (#842). A week the plan says nothing about used to be one
// byte-identical `else` branch whether the plan's own span covered it (a real
// gap) or the week simply fell past however far the plan reaches — and the
// week detail printed "Shape only, not written yet" for BOTH, an outright
// false claim for the second: the coach never sketched a week past the plan's
// end. `week.coverage` now tells the two apart. `empty` still reads as a gap
// (a faint solid tick, distinct from the sketched week's hollow-dashed one).
// `beyond_plan` carries no tick at all and its whole row is visually muted —
// and once the first `beyond_plan` week is reached every later week is one
// too (the run is contiguous), so a single "Plan ends here" divider drawn
// right before it is the whole boundary; it only appears when the plan
// actually ends inside the visible window.
//
// EXPANDING A ROW. A week's detail opens IN PLACE under its bar, on a tap. Not a
// tooltip: this is a phone-first app and hover does not exist on touch. One row
// at a time, and the panel is inset under the bar rather than replacing it — the
// bars above and below keep their geometry, so the ramp still reads while a week
// is open. The panel says the same things in every segmentation mode, because it
// is the week's detail and not a second view of whichever band is drawn.
//
// ACCESSIBILITY. The rows used to be `aria-hidden` with the table below as their
// text alternative. A focusable control cannot live inside an aria-hidden
// subtree, so making the rows tappable moved that boundary: each row is now a
// real button labelled with everything its bar encodes, and the table is drawn
// only when the reader asks for it. Every value still has a text home; it is now
// read once rather than twice.

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import type { GoalRace, HorizonWeek, ScheduleHorizon } from "@/lib/types/schedule";
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

// The Plan column's short word, one per coverage (#842) — the numbers table's
// four-valued reading of the same state the row's tick and detail panel show.
const PLAN_COLUMN_TEXT: Record<HorizonWeek["coverage"], string> = {
  planned: "Sessions",
  sketched: "Shape only",
  empty: "Nothing planned",
  beyond_plan: "Past the end of the plan",
};

// The week-detail's own sentence, one per coverage (#842). `sketched` keeps
// the literal "Shape only, not written yet" — now true of sketched weeks
// alone, since it used to be printed for `beyond_plan` weeks too, which the
// coach never had a chance to sketch.
function coverageDetailText(coverage: HorizonWeek["coverage"]): string {
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
    <span className={`flex ${height} gap-[2px] overflow-hidden rounded-r-[3px] ${surface}`}>
      {segments.map((s) => (
        <span
          key={s.key}
          className={`block ${s.fill}`}
          // flex-grow against a zero basis: the shares divide the bar's own
          // length and cannot overflow it, however they round.
          style={{ flexGrow: s.share, flexBasis: 0, minWidth: 0 }}
        />
      ))}
    </span>
  );
}

const GRID_STOPS = [25, 50, 75];

// Five columns: date · planned tick · bar · running km · the expand chevron.
const ROW_GRID =
  "grid grid-cols-[3.25rem_0.75rem_minmax(0,1fr)_3rem_1rem] items-center gap-x-2";

export default function HorizonChart({
  horizon,
  segmentation,
  showTable,
}: {
  horizon: ScheduleHorizon;
  segmentation: Segmentation;
  showTable: boolean;
}) {
  // One week open at a time. Keyed by `week_start`, which is the row's identity
  // — so a range change that drops the open week simply closes it.
  const [openWeek, setOpenWeek] = useState<string | null>(null);

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
  let boundaryDrawn = false;

  for (const week of horizon.weeks) {
    const disc = disciplineSegments(week.discipline_mix);
    const intent = intentSegments(week.intent_mix);
    const load = week.effort_score ?? 0;
    const length = peak > 0 && load > 0 ? Math.min(100, (load / peak) * 100) : 0;
    const open = openWeek === week.week_start;
    const panelId = `horizon-detail-${week.week_start}`;
    const beyondPlan = week.coverage === "beyond_plan";

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

    // The boundary is drawn once, right before the FIRST beyond_plan week —
    // every week after it is beyond_plan too, since the run is contiguous.
    if (beyondPlan && !boundaryDrawn) {
      rows.push(
        <div
          key="plan-boundary"
          role="separator"
          aria-label="Plan ends here"
          className="my-2 flex items-center gap-2"
        >
          <span className="h-px flex-1 bg-gray-300 dark:bg-gray-600" aria-hidden="true" />
          <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
            Plan ends here
          </span>
          <span className="h-px flex-1 bg-gray-300 dark:bg-gray-600" aria-hidden="true" />
        </div>,
      );
      boundaryDrawn = true;
    }

    // The row's own surface, which is also what shows through the 2px gaps
    // between segments. The current week is tinted, and an open week is tinted
    // again — so the gaps have to be tinted with it or every gap would read as a
    // white seam. One value drives both, which is why there is no hover tint:
    // the gaps cannot follow it.
    const surface = open
      ? "bg-gray-100 dark:bg-gray-700/60"
      : week.is_current
        ? "bg-blue-50 dark:bg-blue-950/50"
        : "bg-white dark:bg-gray-800";

    // Same single source as the detail panel (#842). A screen reader used to
    // hear a flat "nothing planned" for any week with no load, which is a lie
    // for a SKETCHED week whose shape carries no target load — the very
    // two-independent-load-checks contradiction fixed in `WeekDetail` below,
    // left in the aria path. One function, so the two cannot disagree again.
    const zeroLoadCopy = coverageDetailText(week.coverage).toLowerCase();
    const summary = [
      `Week of ${formatDateLabel(week.week_start)}`,
      load > 0 ? `${Math.round(load)} load` : zeroLoadCopy,
      week.running_distance_m ? `${km(week.running_distance_m)} km running` : null,
      week.long_run_distance_m ? `${km(week.long_run_distance_m)} km long run` : null,
      week.quality_focus ? `focus: ${week.quality_focus}` : null,
      disc.length ? `discipline: ${describeMix(disc)}` : null,
      intent.length ? `intent: ${describeMix(intent)}` : null,
    ]
      .filter(Boolean)
      .join(" · ");

    rows.push(
      <div key={week.week_start}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={summary}
          title={summary}
          onClick={() => setOpenWeek(open ? null : week.week_start)}
          // A beyond_plan row is NOT dimmed with opacity. It reads as past the
          // plan already: no tick, no bar, an em dash for km, and the "Plan
          // ends here" divider directly above it. Fading it as well cost the
          // one thing on the row that still carries information — the week's
          // date sits at text-gray-500 (4.83:1) and `opacity-50` drove it to
          // 1.97:1, under the 4.5:1 floor, in the same batch as #843's
          // contrast fix.
          className={`group w-full rounded-sm py-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${surface}`}
        >
          <span className={ROW_GRID}>
            <span
              className={`font-mono text-[11px] tabular-nums ${
                week.is_current
                  ? "font-semibold text-blue-800 dark:text-blue-300"
                  : "text-gray-500 dark:text-gray-400"
              }`}
            >
              {formatDateLabel(week.week_start)}
            </span>

            {/* Solid = real sessions, hollow-dashed = shape only, a faint dot =
                a gap the plan says nothing about, no tick at all = past the
                plan's own end (#842). A tick rather than a fade for the first
                three: fading every non-planned row washes out most of the
                chart, which is exactly why `beyond_plan` gets no mark instead —
                its whole row is already muted below. */}
            <span className="flex justify-center" aria-hidden="true">
              {week.coverage === "planned" ? (
                <span className="h-2 w-2 rounded-full bg-gray-600 dark:bg-gray-300" />
              ) : week.coverage === "sketched" ? (
                <span className="block h-2 w-2 rounded-full border border-dashed border-gray-400 dark:border-gray-500" />
              ) : week.coverage === "empty" ? (
                <span className="h-1 w-1 rounded-full bg-gray-300 dark:bg-gray-600" />
              ) : null}
            </span>

            <span
              className={`relative block ${both ? "h-5" : "h-3.5"} ${surface}`}
              aria-hidden="true"
            >
              {GRID_STOPS.map((stop) => (
                <span
                  key={stop}
                  className="absolute inset-y-0 w-px bg-gray-200 dark:bg-gray-700"
                  style={{ left: `${stop}%` }}
                />
              ))}
              {length > 0 && (
                <span
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
                </span>
              )}
            </span>

            <span
              className={`text-right font-mono text-[11px] tabular-nums ${
                week.is_current
                  ? "font-semibold text-blue-800 dark:text-blue-300"
                  : "text-gray-600 dark:text-gray-300"
              }`}
            >
              {km(week.running_distance_m)}
            </span>

            <span className="flex justify-end" aria-hidden="true">
              <ChevronDown
                size={12}
                className={`text-gray-400 transition-transform group-hover:text-gray-700 dark:text-gray-500 dark:group-hover:text-gray-200 ${
                  open ? "rotate-180" : ""
                }`}
              />
            </span>
          </span>
        </button>

        {open && (
          <WeekDetail
            id={panelId}
            week={week}
            peak={peak}
            disciplines={disc}
            intents={intent}
          />
        )}
      </div>,
    );

    for (const race of racesByWeek.get(week.week_start) ?? []) {
      rows.push(
        <div key={race.id} className={`${ROW_GRID} py-0.5`}>
          <span className="font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {formatDateLabel(race.race_date)}
          </span>
          <span className="flex justify-center" aria-hidden="true">
            <span className="h-2 w-0.5 bg-rose-700 dark:bg-rose-400" />
          </span>
          <span className="flex items-center gap-2 truncate text-[11px] font-medium text-rose-700 dark:text-rose-400">
            <span className="h-px w-3 shrink-0 bg-rose-700 dark:bg-rose-400" aria-hidden="true" />
            <span className="truncate">{race.name}</span>
          </span>
          <span className="text-right font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {(race.distance_m / 1000).toFixed(0)}k
          </span>
          <span />
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
          real sessions, hollow tick = shape only, faint dot = nothing planned.
          Rows past a &ldquo;Plan ends here&rdquo; line carry no tick at all — your
          coach has not reached that far yet. The number on the right is running
          km, so a long bar beside a small number is a week you cross-train. Tap
          any week for its detail.
        </p>
      </div>

      {showTable && (
        <HorizonTable
          horizon={horizon}
          peak={peak}
          unplacedRaces={unplacedRaces}
          className="mt-6"
        />
      )}
    </div>
  );
}

/**
 * One week's detail, opened in place.
 *
 * Inset under the bar rather than replacing it, and deliberately short: rows
 * below shift down, but no bar changes length, so the ramp survives being read
 * with a week open. It carries the same four facts in every segmentation mode —
 * this is the week, not a second view of whichever band happens to be drawn.
 */
function WeekDetail({
  id,
  week,
  peak,
  disciplines,
  intents,
}: {
  id: string;
  week: HorizonWeek;
  peak: number;
  disciplines: Segment[];
  intents: Segment[];
}) {
  const load = week.effort_score ?? 0;
  const share = peak > 0 && load > 0 ? Math.round((load / peak) * 100) : 0;
  const beyondPlan = week.coverage === "beyond_plan";
  // One status line per coverage (#842) — the load figure and the coverage
  // sentence used to be printed as two independent load>0 checks, which meant
  // a zero-load week could read "Nothing planned" right next to "Shape only,
  // not written yet" for the very same week. `coverageDetailText` is now the
  // single source of that sentence, so the two can never disagree.
  // Italic, not faint: italic distinguishes without spending contrast, and the
  // sentence still has to be readable (text-gray-400 is 2.54:1 on this panel,
  // under the 4.5:1 floor — see the row button above).
  const coverageClass =
    week.coverage === "planned"
      ? "text-gray-600 dark:text-gray-300"
      : beyondPlan
        ? "italic text-gray-500 dark:text-gray-400"
        : "text-gray-500 dark:text-gray-400";

  return (
    <div
      id={id}
      // No opacity here either, for the reason given on the row button: the
      // panel is all 11px text, and `opacity-70` drove the coverage sentence
      // to 2.70:1 and the phase heading to 4.28:1, both under the 4.5:1 floor.
      // "Past the end of the plan" is the sentence #842 exists to say, so it
      // is the last thing that should be hard to read. Italic carries it.
      className="ml-[4rem] mr-[1rem] mt-1 rounded-md border-l-2 border-gray-300 bg-gray-50 px-3 py-2.5 text-[11px] dark:border-gray-600 dark:bg-gray-900/50"
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-medium text-gray-700 dark:text-gray-200">
          {week.phase ?? "No phase"}
        </span>
        <span className="text-gray-600 dark:text-gray-300">
          <span className="font-mono tabular-nums">{km(week.running_distance_m)}</span> km
          running
        </span>
        {/* #980: the long run, so a build reads as a build past the concrete
            weeks. Omitted entirely rather than shown as "— km" when null,
            since a week can genuinely hold no long run, and that is a
            different claim from "not written yet". */}
        {week.long_run_distance_m != null && week.long_run_distance_m > 0 && (
          <span className="text-gray-600 dark:text-gray-300">
            <span className="font-mono tabular-nums">{km(week.long_run_distance_m)}</span> km
            long run
          </span>
        )}
        {load > 0 && (
          <span className="text-gray-600 dark:text-gray-300">
            <span className="font-mono tabular-nums">{Math.round(load)}</span> load
            {" · "}
            <span className="font-mono tabular-nums">{share}%</span> of your biggest
            week
          </span>
        )}
        <span className={coverageClass}>{coverageDetailText(week.coverage)}</span>
        {/* #980: what the week's hard session is for. Only ever set on a
            sketched week (a planned week states its quality work in the
            sessions themselves), so this never contradicts "Shape only". */}
        {week.quality_focus && (
          <span className="italic text-gray-500 dark:text-gray-400">
            Focus: {week.quality_focus}
          </span>
        )}
      </div>

      <dl className="mt-2 space-y-1">
        <SplitRow label="Discipline" segments={disciplines} />
        <SplitRow label="Intent" segments={intents} />
      </dl>
    </div>
  );
}

/** A mix written out as named percentages, each beside its own swatch. */
function SplitRow({ label, segments }: { label: string; segments: Segment[] }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <dt className="w-[4.5rem] shrink-0 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
        {label}
      </dt>
      {segments.length === 0 ? (
        <dd className="text-gray-500 dark:text-gray-400">—</dd>
      ) : (
        segments.map((s) => (
          <dd
            key={s.key}
            className="flex items-center gap-1.5 text-gray-600 dark:text-gray-300"
          >
            <span className={`h-2 w-2 shrink-0 rounded-sm ${s.fill}`} aria-hidden="true" />
            {s.label} <span className="font-mono tabular-nums">{pct(s.share)}%</span>
          </dd>
        ))
      )}
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
          <span className={`h-2 w-2 shrink-0 rounded-sm ${s.fill}`} aria-hidden="true" />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/**
 * Every week in one table — the numbers, written out.
 *
 * Drawn on request rather than always present: the rows above are now labelled
 * buttons carrying the same values, so keeping a permanently sr-only copy would
 * make a screen reader read the whole horizon twice.
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
    <div className={className} id="horizon-numbers">
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
                Long run
              </th>
              <th scope="col" className={head}>
                Focus
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
                  <td className={cell}>{PLAN_COLUMN_TEXT[week.coverage]}</td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {load > 0 ? `${share}% of peak` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {week.running_distance_m ? `${km(week.running_distance_m)} km` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {week.long_run_distance_m ? `${km(week.long_run_distance_m)} km` : "—"}
                  </td>
                  <td className={cell}>{week.quality_focus ?? "—"}</td>
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
