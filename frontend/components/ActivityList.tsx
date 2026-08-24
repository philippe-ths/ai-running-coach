import Link from 'next/link';
import { format } from 'date-fns';
import { ChevronRight, MessageSquareQuote } from 'lucide-react';

import { activityStartDate, hasMeaningfulDistance } from '@/lib/format';
import { disciplineForActivityType, groupActivitiesByDay } from '@/lib/activityGrouping';
import { DISCIPLINE_LABEL } from '@/components/schedule/palette';
import DayGroupHeader from '@/components/activities/DayGroupHeader';

interface Activity {
  id: string;
  name: string;
  type: string;
  start_date: string;
  start_date_local?: string | null;
  distance_m: number;
  moving_time_s: number;
  headline?: string | null;
  // #797: the coach report's opening line. Telegram is the only channel that
  // announces a report, so for a runner who has not linked one this is how they
  // find out the run was coached at all. Absent until a report exists.
  coach_lead?: string | null;
  // #947: DerivedMetric.effort_score, projected at read time. Null until the
  // activity has been analysed — the day-group header sums this as LOAD.
  effort_score?: number | null;
}

export default function ActivityList({
  activities,
  hasMore = false,
}: {
  activities: Activity[];
  // #947: whether more history beyond `activities` might still be loaded (the
  // "Load more" pager). The list is date-descending, so only the OLDEST group
  // can be split by that boundary — passing this lets that one day say its
  // totals are not final rather than silently under-reporting a day the
  // runner just hasn't finished loading.
  hasMore?: boolean;
}) {
  if (!activities || activities.length === 0) {
    return <div className="text-gray-500 dark:text-gray-400 italic">No recent activities found. Try syncing.</div>;
  }

  // #947: re-grouped on every render over the WHOLE accumulated list (not
  // per page), so a day split across a "Load more" boundary merges into one
  // group once both pages are in.
  const groups = groupActivitiesByDay(activities, hasMore);

  return (
    <div>
      {groups.map((group) => {
        const disciplines = new Set(group.activities.map((a) => disciplineForActivityType(a.type)));
        const mixedDay = disciplines.size > 1;
        return (
          <div key={group.dayKey}>
            <DayGroupHeader group={group} />
            <div className="space-y-3">
              {group.activities.map((activity) => (
                <Link
                  key={activity.id}
                  href={`/activity/${activity.id}`}
                  className="block bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:shadow-md transition-shadow"
                >
                  <div className="flex justify-between items-center gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <h3 className="font-semibold text-lg text-gray-900 dark:text-gray-100 truncate">{activity.name}</h3>
                        {activity.headline && (
                          <span className="inline-block px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 text-xs font-medium shrink-0">
                            {activity.headline}
                          </span>
                        )}
                      </div>
                      {/* The date no longer repeats here (#947) — the day
                          header above already carries it, and the group is
                          what says "this card belongs to that day". The
                          discipline label (the LoggedList precedent, text
                          rather than colour — DISCIPLINE_FILL's stepped hue
                          ramp is legible in the schedule's wide mix bar but
                          not at this card's small scale) only earns its keep
                          on a MIXED day: the day a run, a ride and a strength
                          session need telling apart at a glance. A
                          single-discipline day already says so in its header
                          count. */}
                      <div className="text-sm text-gray-500 dark:text-gray-400 flex flex-wrap gap-x-3 gap-y-1 mt-1">
                        {mixedDay && (
                          <>
                            <span className="font-medium text-gray-600 dark:text-gray-300">
                              {DISCIPLINE_LABEL[disciplineForActivityType(activity.type)]}
                            </span>
                            <span aria-hidden="true">•</span>
                          </>
                        )}
                        <span>{format(activityStartDate(activity), 'h:mm a')}</span>
                        {hasMeaningfulDistance(activity.distance_m) && (
                          <>
                            <span aria-hidden="true">•</span>
                            <span>{(activity.distance_m / 1000).toFixed(2)} km</span>
                          </>
                        )}
                        <span aria-hidden="true">•</span>
                        <span>{Math.floor(activity.moving_time_s / 60)} min</span>
                      </div>
                      {/* font-serif matches how coach prose reads in CoachSheet and
                          CoachReportPanel, so the coach's voice looks the same wherever
                          it appears rather than like list metadata. */}
                      {activity.coach_lead && (
                        <p className="mt-2 flex items-start gap-2 font-serif text-sm leading-snug text-gray-700 dark:text-gray-300">
                          <MessageSquareQuote
                            size={15}
                            aria-hidden="true"
                            className="mt-0.5 shrink-0 text-gray-400 dark:text-gray-500"
                          />
                          <span className="line-clamp-2">{activity.coach_lead}</span>
                        </p>
                      )}
                    </div>
                    <ChevronRight className="text-gray-400 dark:text-gray-500 shrink-0" />
                  </div>
                </Link>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
