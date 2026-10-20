// The horizon chart's presentational pieces: bands, challenge lines, the week
// detail, the legend and the numbers table. Split out of HorizonChart.tsx
// (#1097), whose header explains the design they implement.

import type { GoalRace, HorizonChallenge, HorizonWeek, ScheduleHorizon } from "@/lib/types/schedule";
import { formatDateLabel } from "@/lib/format";
import {
  type Segment,
  CHALLENGE_TONE,
  PLAN_COLUMN_TEXT,
  activitySegments,
  goalDayLabel,
  kindSegments,
  coverageDetailText,
  describeMix,
  hours,
  km,
  longRun,
  pct,
  readChallenge,
} from "./horizon";

/** One band of the bar: the segments, separated by 2px of the row's surface. */
export function Band({ segments, surface }: { segments: Segment[]; surface: string }) {
  return (
    <span className={`flex h-3.5 w-full gap-[2px] overflow-hidden rounded-r-[3px] ${surface}`}>
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

// Five columns: date · planned tick · bar · hours and km · the expand chevron.
// The figures column is wide enough for "run 30 · walk 15 km" at 10px.
export const ROW_GRID =
  "grid grid-cols-[3rem_0.75rem_minmax(0,1fr)_7rem_0.75rem] items-center gap-x-2";


export function ChallengeLines({
  challenges,
  className = "",
}: {
  challenges: HorizonChallenge[];
  className?: string;
}) {
  const withName = challenges.length > 1;
  return (
    <span className={`flex flex-col gap-0.5 ${className}`}>
      {challenges.map((c) => {
        const r = readChallenge(c, withName);
        return (
          <span
            key={c.goal_id}
            className={`flex flex-wrap items-baseline gap-x-2 text-[11px] ${CHALLENGE_TONE[r.state]}`}
          >
            <span className="font-mono tabular-nums">{r.figures}</span>
            <span className="font-semibold">
              <span aria-hidden="true">{r.symbol}</span> {r.word}
            </span>
            <span className="font-mono text-[10px] tabular-nums text-gray-500 dark:text-gray-400">
              {r.position}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/**
 * One week's detail, opened in place.
 *
 * Inset under the bar rather than replacing it, and deliberately short: rows
 * below shift down, but no bar changes length, so the ramp survives being read
 * with a week open. It is also the only home of the kind-of-session split.
 */
export function WeekDetail({
  id,
  week,
  peak,
  activities,
  kinds,
}: {
  id: string;
  week: HorizonWeek;
  peak: number;
  activities: Segment[];
  kinds: Segment[];
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
        {week.phase && (
          <span className="font-medium text-gray-700 dark:text-gray-200">{week.phase}</span>
        )}
        {week.duration_s != null && week.duration_s > 0 && (
          <span className="text-gray-600 dark:text-gray-300">
            <span className="font-mono tabular-nums">{hours(week.duration_s)}</span> hours
          </span>
        )}
        <span className="text-gray-600 dark:text-gray-300">
          <span className="font-mono tabular-nums">{km(week.running_distance_m)}</span> km
          running
        </span>
        {week.walking_distance_m != null && week.walking_distance_m > 0 && (
          <span className="text-gray-600 dark:text-gray-300">
            <span className="font-mono tabular-nums">{km(week.walking_distance_m)}</span> km
            walking
          </span>
        )}
        {/* #980: the long run, so a build reads as a build past the concrete
            weeks. Omitted entirely rather than shown as "— km" when null,
            since a week can genuinely hold no long run, and that is a
            different claim from "not written yet". */}
        {longRun(week) && (
          <span className="text-gray-600 dark:text-gray-300">
            <span className="font-mono tabular-nums">{longRun(week)}</span> long run
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

      {(week.challenges ?? []).length > 0 && (
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="w-[6rem] shrink-0 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
            Challenge
          </span>
          <ChallengeLines challenges={week.challenges ?? []} />
        </div>
      )}

      <dl className="mt-2 space-y-1">
        <SplitRow label="Activity" segments={activities} />
        <SplitRow label="Kind of session" segments={kinds} swatch={false} />
      </dl>
    </div>
  );
}

/** A mix written out as named percentages, each beside its own swatch. */
export function SplitRow({
  label,
  segments,
  swatch = true,
}: {
  label: string;
  segments: Segment[];
  swatch?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <dt className="w-[6rem] shrink-0 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
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
            {swatch && (
              <span className={`h-2 w-2 shrink-0 rounded-sm ${s.fill}`} aria-hidden="true" />
            )}
            {s.label} <span className="font-mono tabular-nums">{pct(s.share)}%</span>
          </dd>
        ))
      )}
    </div>
  );
}

export function LegendRow({ title, items }: { title: string; items: Segment[] }) {
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
export function HorizonTable({
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
        <table className="w-full min-w-[44rem] border-collapse text-[11px] text-gray-600 dark:text-gray-300">
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
                Hours
              </th>
              <th scope="col" className={head}>
                Running km
              </th>
              <th scope="col" className={head}>
                Walking km
              </th>
              <th scope="col" className={head}>
                Long run
              </th>
              <th scope="col" className={head}>
                Focus
              </th>
              <th scope="col" className={head}>
                Activity
              </th>
              <th scope="col" className={head}>
                Kind of session
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
                    {week.duration_s ? `${hours(week.duration_s)} h` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {week.running_distance_m ? `${km(week.running_distance_m)} km` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {week.walking_distance_m ? `${km(week.walking_distance_m)} km` : "—"}
                  </td>
                  <td className={`${cell} whitespace-nowrap font-mono tabular-nums`}>
                    {longRun(week) ?? "—"}
                  </td>
                  <td className={cell}>{week.quality_focus ?? "—"}</td>
                  <td className={cell}>{describeMix(activitySegments(week.discipline_mix))}</td>
                  <td className={cell}>{describeMix(kindSegments(week.intent_mix))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {horizon.races.length > 0 && (
        <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
          Goals in this window:{" "}
          {horizon.races
            .map(
              (r) =>
                `${r.name} ${r.race_date ? "on " : ""}${goalDayLabel(r)}` +
                (r.distance_m ? ` (${(r.distance_m / 1000).toFixed(0)} km)` : ""),
            )
            .join("; ")}
          .
          {unplacedRaces.length > 0 && " Some fall outside the weeks shown."}
        </p>
      )}
    </div>
  );
}
