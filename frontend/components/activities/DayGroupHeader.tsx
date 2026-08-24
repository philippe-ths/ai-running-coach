// #947: the day a group of activities belongs to, with the day's own totals.
//
// TIME and LOAD are the two figures that total a mixed day honestly — distance
// cannot, since a bike or a strength session logs none. LOAD is `effort_score`,
// the one scale that sums across every discipline with or without HR (#186).
//
// A day's totals must never look complete when they are not (#947 AC): the
// oldest visible day can be cut by the "Load more" page boundary
// (`boundaryPartial`), and any day can hold an activity still awaiting
// analysis (`loadComplete === false`). Both read as the same quiet amber
// "Partial" mark already used for a schedule rule violation — information,
// not an alarm.

import { formatDuration } from '@/lib/format';
import { dayHeading, type DayGroup, type GroupableActivity } from '@/lib/activityGrouping';

function loadLabel(totalLoad: number, loadComplete: boolean): string {
  const rounded = Math.round(totalLoad);
  return loadComplete ? `${rounded} load` : `${rounded}+ load`;
}

export default function DayGroupHeader<T extends GroupableActivity>({
  group,
}: {
  group: DayGroup<T>;
}) {
  const partial = group.boundaryPartial || !group.loadComplete;
  const count = group.activities.length;

  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-1 pt-4 pb-1.5 first:pt-1">
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          {dayHeading(group.date)}
        </h2>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          {count} {count === 1 ? 'activity' : 'activities'}
        </span>
      </div>

      <div className="flex items-center gap-2.5 text-xs">
        <span className="font-mono tabular-nums text-gray-600 dark:text-gray-300">
          {formatDuration(group.totalTimeS)}
        </span>
        <span className="text-gray-300 dark:text-gray-600" aria-hidden="true">
          ·
        </span>
        <span className="font-mono tabular-nums text-gray-600 dark:text-gray-300">
          {loadLabel(group.totalLoad, group.loadComplete)}
        </span>
        {partial && (
          <span
            className="inline-flex items-center rounded-full bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300"
            title={
              group.boundaryPartial
                ? 'More activities from this day may still be waiting — load more to see the full totals.'
                : "Load doesn't include an activity that hasn't been analysed yet."
            }
          >
            Partial
          </span>
        )}
      </div>
    </div>
  );
}
