// #830: what the runner actually did this week.
//
// The free week's session list, and the receipt underneath a planned one. The
// rows come from the same fact stream the headline is summed from, so this list
// can never be a second opinion about the week's totals.

import Link from "next/link";
import type { LoggedActivity } from "@/lib/types/schedule";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import { DISCIPLINE_LABEL, safeDiscipline } from "./palette";
import { formatDayChip } from "./dates";

function measure(a: LoggedActivity): string {
  const parts: string[] = [];
  if (a.distance_m > 0) parts.push(formatDistanceKm(a.distance_m));
  if (a.moving_time_s > 0) parts.push(formatDuration(a.moving_time_s));
  // No fallback to `effort_score`. An activity with neither a distance nor a
  // duration is already named by its type and its day; "38 load" adds a
  // modelled number the runner can neither act on nor check.
  return parts.join(" · ");
}

export default function LoggedList({ logged }: { logged: LoggedActivity[] }) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Logged this week</h2>

      {logged.length === 0 ? (
        <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
          Nothing logged yet this week.
        </p>
      ) : (
        <ul className="mt-1 divide-y divide-gray-200 dark:divide-gray-700">
          {logged.map((a, i) => {
            const row = (
              <div className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-sm text-gray-900 dark:text-gray-100">
                    {a.activity_type}
                  </div>
                  <div className="text-[11px] text-gray-400 dark:text-gray-500">
                    {formatDayChip(a.local_date)} ·{" "}
                    {DISCIPLINE_LABEL[safeDiscipline(a.discipline)]}
                  </div>
                </div>
                <span className="shrink-0 font-mono text-xs tabular-nums text-gray-600 dark:text-gray-300">
                  {measure(a)}
                </span>
              </div>
            );
            return (
              <li key={a.activity_id ?? `${a.local_date}-${i}`}>
                {a.activity_id ? (
                  <Link
                    href={`/activity/${a.activity_id}`}
                    className="-mx-2 block rounded-md px-2 hover:bg-gray-50 dark:hover:bg-gray-700/50"
                  >
                    {row}
                  </Link>
                ) : (
                  row
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
