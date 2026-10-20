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
// ONE BAR, split by ACTIVITY (run, walk, bike, ...), each its own hue. The kind
// of session (easy / long / quality / strength) is the same load seen a second
// way and was a thin second band; it now lives only in the opened week's detail,
// so the row answers one question and the reader never has to learn two colour
// languages.
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
// is open. The panel is the week's full detail, including the kind-of-session split.
//
// CHALLENGE LINES (#1064). A week a challenge covers ("10 h of zone 2+ every week
// for 10 weeks") carries one line under its bar: what the week holds against
// what it needs, and once the week is past, what the runner actually did. The
// mark is a symbol AND a word, never colour alone: a tick for met (or a plan
// that meets it), a cross for a missed week, a triangle for a plan that falls
// short. The plan's own shortfalls are stated above the chart, never left out.
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
import type { GoalRace, ScheduleHorizon } from "@/lib/types/schedule";
import { formatDateLabel } from "@/lib/format";
import { addDaysIso } from "./dates";
import {
  type Segment,
  activitySegments,
  coverageDetailText,
  describeChallenges,
  describeMix,
  distanceLine,
  goalColumnLabel,
  goalDay,
  goalDayLabel,
  hours,
  km,
  kindSegments,
  longRun,
  weekInWindow,
} from "./horizon";
import {
  Band,
  ChallengeLines,
  HorizonTable,
  LegendRow,
  ROW_GRID,
  WeekDetail,
} from "./HorizonParts";

export default function HorizonChart({
  horizon,
  showTable,
}: {
  horizon: ScheduleHorizon;
  showTable: boolean;
}) {
  // One week open at a time. Keyed by `week_start`, which is the row's identity
  // — so a range change that drops the open week simply closes it.
  const [openWeek, setOpenWeek] = useState<string | null>(null);

  const peak = horizon.peak_effort_score ?? 0;

  // A race lands in the week whose seven days contain it. Weeks are contiguous
  // and the API only returns races inside the span, so a race that matches no
  // week is a mismatch worth not silently dropping — it falls to the tail.
  // A goal with only an approximate date ("~March") stands at the start of its
  // window, which is the date the plan works to (#1042). The API leaves out goals
  // with no date at all; the goals panel lists those.
  const racesByWeek = new Map<string, GoalRace[]>();
  const placed = new Set<string>();
  for (const race of horizon.races) {
    // A window that opened before the first week shown is under way, so it
    // stands in the first week rather than falling off the chart.
    const first = horizon.weeks[0]?.week_start;
    const raw = goalDay(race);
    const day = raw && first && !race.race_date && raw < first ? first : raw;
    const week = day
      ? horizon.weeks.find((w) => day >= w.week_start && day <= addDaysIso(w.week_start, 6))
      : undefined;
    if (!week) continue;
    placed.add(race.id);
    const list = racesByWeek.get(week.week_start) ?? [];
    list.push(race);
    racesByWeek.set(week.week_start, list);
  }
  const unplacedRaces = horizon.races.filter((r) => !placed.has(r.id));

  // A range goal ("10h a week, Oct–Dec") runs THROUGH weeks rather than landing
  // on one, so every week it covers carries a thin rail down the left edge.
  const windowGoals = horizon.races.filter((r) => !r.race_date && r.window_start && r.window_end);
  const railFor = (weekStart: string) =>
    windowGoals.some((g) => weekInWindow(weekStart, g)) ? (
      <span
        className="pointer-events-none absolute -left-2 inset-y-0 w-0.5 rounded bg-rose-500/70 dark:bg-rose-400/70"
        aria-hidden="true"
      />
    ) : null;

  const rows: ReactNode[] = [];
  let lastPhase: string | null = null;
  let boundaryDrawn = false;

  for (const week of horizon.weeks) {
    const activity = activitySegments(week.discipline_mix);
    const kinds = kindSegments(week.intent_mix);
    const load = week.effort_score ?? 0;
    const length = peak > 0 && load > 0 ? Math.min(100, (load / peak) * 100) : 0;
    const open = openWeek === week.week_start;
    const panelId = `horizon-detail-${week.week_start}`;
    const beyondPlan = week.coverage === "beyond_plan";
    const challenges = week.challenges ?? [];

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
      week.duration_s ? `${hours(week.duration_s)} hours, every activity` : null,
      week.running_distance_m ? `${km(week.running_distance_m)} km running` : null,
      week.walking_distance_m ? `${km(week.walking_distance_m)} km walking` : null,
      longRun(week) ? `${longRun(week)} long run` : null,
      week.quality_focus ? `focus: ${week.quality_focus}` : null,
      activity.length ? `activity: ${describeMix(activity)}` : null,
      challenges.length ? describeChallenges(challenges) : null,
    ]
      .filter(Boolean)
      .join(" · ");

    rows.push(
      <div key={week.week_start} className="relative">
        {railFor(week.week_start)}
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

            <span className={`relative block h-3.5 ${surface}`} aria-hidden="true">
              {length > 0 && (
                <span
                  className={`absolute inset-y-0 left-0 flex ${surface}`}
                  style={{ width: `${length}%` }}
                >
                  <Band segments={activity} surface={surface} />
                </span>
              )}
            </span>

            <span
              className={`flex flex-col items-end leading-tight tabular-nums ${
                week.is_current
                  ? "font-semibold text-blue-800 dark:text-blue-300"
                  : "text-gray-600 dark:text-gray-300"
              }`}
            >
              <span className="font-mono text-[11px]">
                {hours(week.duration_s)}
                {week.duration_s ? " h" : ""}
              </span>
              {distanceLine(week) && (
                <span className="whitespace-nowrap font-mono text-[10px] font-normal text-gray-500 dark:text-gray-400">
                  {distanceLine(week)}
                </span>
              )}
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

        {/* The challenge line sits under its week's bar, aligned with the bar. It
            is hidden from a screen reader because the row's label already says
            it; the opened detail repeats it in full for everyone. */}
        {challenges.length > 0 && (
          <div className={`pb-1 pl-[4.75rem] pr-1 ${surface}`} aria-hidden="true">
            <ChallengeLines challenges={challenges} />
          </div>
        )}

        {open && (
          <WeekDetail
            id={panelId}
            week={week}
            peak={peak}
            activities={activity}
            kinds={kinds}
          />
        )}
      </div>,
    );

    for (const race of racesByWeek.get(week.week_start) ?? []) {
      rows.push(
        <div key={race.id} className={`relative ${ROW_GRID} py-0.5`}>
          {railFor(week.week_start)}
          <span className="font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {goalColumnLabel(race)}
          </span>
          <span className="flex justify-center" aria-hidden="true">
            <span className="h-2 w-0.5 bg-rose-700 dark:bg-rose-400" />
          </span>
          <span className="flex items-center gap-2 truncate text-[11px] font-medium text-rose-700 dark:text-rose-400">
            <span className="h-px w-3 shrink-0 bg-rose-700 dark:bg-rose-400" aria-hidden="true" />
            <span className="truncate">
              {race.name}
              {!race.race_date && (
                <span className="font-normal opacity-80"> · {goalDayLabel(race).slice(1)}</span>
              )}
            </span>
          </span>
          <span className="text-right font-mono text-[11px] tabular-nums text-rose-700 dark:text-rose-400">
            {race.distance_m ? `${(race.distance_m / 1000).toFixed(0)}k` : ""}
          </span>
          <span />
        </div>,
      );
    }
  }

  // The legend covers exactly the activities actually on the chart. Identity
  // never rests on colour alone: every swatch is named here and in the table.
  const legendActivities = new Map<string, Segment>();
  for (const week of horizon.weeks) {
    for (const s of activitySegments(week.discipline_mix)) legendActivities.set(s.key, s);
  }

  const hasAnyLoad = peak > 0;

  const shortfalls = horizon.shortfalls ?? [];
  const hasChallenge = horizon.weeks.some((w) => (w.challenges ?? []).length > 0);

  return (
    <div>
      {/* #1064: what the plan cannot do, said above the weeks it concerns. Never
          folded into the chart or left to the numbers table. */}
      {shortfalls.length > 0 && (
        <div
          role="note"
          className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
        >
          <p className="font-semibold">
            {shortfalls.length === 1
              ? "One thing your plan cannot do yet"
              : "Things your plan cannot do yet"}
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {shortfalls.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div
        className={`${ROW_GRID} pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400`}
      >
        <span>Week</span>
        <span />
        <span>Training load per week</span>
        <span className="text-right normal-case tracking-normal">hours · km</span>
        <span />
      </div>

      <div className="space-y-0.5">{rows}</div>

      {/* The scale. Bars are true proportions of the peak week, so the reader
          is owed the number the proportion is of. */}
      <div className={`${ROW_GRID} mt-2 border-t border-gray-100 pt-1.5 dark:border-gray-700`}>
        <span />
        <span />
        <span className="flex justify-between text-[10px] text-gray-500 dark:text-gray-400">
          <span className="font-mono tabular-nums">0</span>
          <span>
            {hasAnyLoad ? (
              <>
                your biggest week:{" "}
                <span className="font-mono tabular-nums">{Math.round(peak)}</span> load
              </>
            ) : (
              "no load"
            )}
          </span>
        </span>
        <span className="text-right text-[10px] text-gray-400 dark:text-gray-500">
          {horizon.weeks.some((w) => w.duration_s) ? "hours" : "run km"}
        </span>
        <span />
      </div>

      <div className="mt-3 space-y-1.5">
        {legendActivities.size > 0 && (
          <LegendRow title="Activity" items={Array.from(legendActivities.values())} />
        )}
        <p className="pt-1 text-[11px] text-gray-500 dark:text-gray-400">
          Each bar is the week&rsquo;s training load against your biggest week,
          coloured by activity; the figures on the right are the week&rsquo;s hours
          and km. A solid dot means real sessions, a hollow one shape only, and a
          faint one nothing planned. Tap any week for its detail.
          {hasChallenge &&
            " Under a week, the challenge line shows what the week holds against what it needs, or what you did once it is over: a tick is met, a cross is missed, a triangle is a plan that falls short."}
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

