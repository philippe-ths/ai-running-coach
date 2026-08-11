// #830: the week's headline.
//
// Running km leads, because km is how a runner thinks about running and running
// is the priority sport. The discipline mix sits under it sized by load, so
// strength, bike and row stay visible as progress rather than disappearing for
// having no distance.
//
// FREE MODE is `headline.planned_sessions === 0` — NOT `has_plan === false`. A
// plan carrying only suggestions is still a free week, so the same runner can
// have a plan row and still be reading "km running so far" against their norm.

import { MessageCircle } from "lucide-react";
import NormGauge from "@/components/trends/NormGauge";
import type { ScheduleWeek } from "@/lib/types/schedule";
import type { VolumeMetricVsNorm } from "@/lib/types/volume";
import DisciplineMixBar from "./DisciplineMixBar";

function km(metres: number): string {
  return (metres / 1000).toFixed(1);
}

function findNorm(
  norm: VolumeMetricVsNorm[] | null,
  metric: string,
): VolumeMetricVsNorm | undefined {
  return norm?.find((m) => m.metric === metric);
}

export default function WeekHeader({
  week,
  freeMode,
  onAskCoach,
}: {
  week: ScheduleWeek;
  freeMode: boolean;
  onAskCoach?: () => void;
}) {
  const { headline } = week;
  const distanceNorm = findNorm(week.norm, "distance_m");
  const sessionsNorm = findNorm(week.norm, "sessions");
  const hasGauge =
    freeMode &&
    !!distanceNorm &&
    distanceNorm.direction !== "no_norm" &&
    distanceNorm.norm !== null &&
    distanceNorm.pct_vs_norm !== null;

  const bigMetres = freeMode
    ? headline.logged_running_distance_m
    : headline.planned_running_distance_m ?? headline.logged_running_distance_m;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-4xl font-semibold tabular-nums tracking-tight text-gray-900 dark:text-gray-50">
            {km(bigMetres)}
          </span>
          <span className="text-sm font-medium text-gray-500 dark:text-gray-400">
            {freeMode ? "km running so far" : "km running"}
          </span>
        </div>

        {!freeMode && (
          <div className="text-sm text-gray-500 dark:text-gray-400">
            <span className="font-mono tabular-nums text-gray-700 dark:text-gray-200">
              {km(headline.logged_running_distance_m)}
            </span>{" "}
            done ·{" "}
            <span className="font-mono tabular-nums text-gray-700 dark:text-gray-200">
              {headline.done_sessions}
            </span>{" "}
            of{" "}
            <span className="font-mono tabular-nums text-gray-700 dark:text-gray-200">
              {headline.planned_sessions}
            </span>{" "}
            sessions
          </div>
        )}
      </div>

      {freeMode && (
        <div className="mt-2">
          {hasGauge && distanceNorm && (
            <NormGauge pct={distanceNorm.pct_vs_norm ?? 0} />
          )}
          {distanceNorm?.norm != null && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Your typical week across everything:{" "}
              <span className="font-mono tabular-nums">{km(distanceNorm.norm)} km</span>
              {sessionsNorm?.norm != null && (
                <>
                  {" · "}
                  <span className="font-mono tabular-nums">
                    {Math.round(sessionsNorm.norm)}
                  </span>{" "}
                  sessions
                </>
              )}
            </p>
          )}
          <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
            Typical counts every activity you log, not only your runs — so a week
            of walking counts toward it.
          </p>
        </div>
      )}

      <DisciplineMixBar items={week.by_discipline} mode={freeMode ? "logged" : "planned"} />

      {onAskCoach && (
        <div className="mt-4 border-t border-gray-100 pt-3 dark:border-gray-700">
          <button
            type="button"
            onClick={onAskCoach}
            className="inline-flex items-center gap-2 rounded-md px-2 py-1.5 -ml-2 text-sm font-medium text-blue-700 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30"
          >
            <MessageCircle size={15} aria-hidden="true" />
            Ask your coach about this week
          </button>
        </div>
      )}
    </section>
  );
}
