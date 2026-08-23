/**
 * Groups a date-descending activity list into calendar-day buckets (#947).
 *
 * A day of training — a run, a ride, a strength session — only reads as ONE
 * day when it is grouped under the day it happened, rather than scattered
 * through a flat run of cards. Grouping runs client-side over the WHOLE
 * accumulated list on every render (not per page), so a day that straddles a
 * "Load more" page boundary merges into one group once both pages have
 * loaded, and the merge is stable regardless of where the boundary happened
 * to fall.
 *
 * The day boundary is the runner's LOCAL calendar day — `start_date_local`
 * when present, else the UTC `start_date` — the one `local_day` convention
 * `activity_facts.py` defines server-side (#399/#411). `activityStartDate`
 * already rebuilds that wall-clock instant as a browser-local `Date`, so
 * formatting it needs no further timezone handling here.
 */
import { format } from 'date-fns';
import { activityStartDate } from '@/lib/format';
import type { Discipline } from '@/lib/types/schedule';

export interface GroupableActivity {
  id: string;
  type: string;
  start_date: string;
  start_date_local?: string | null;
  moving_time_s: number;
  effort_score?: number | null;
}

export interface DayGroup<T extends GroupableActivity> {
  /** yyyy-MM-dd, the runner's local day. */
  dayKey: string;
  date: Date;
  activities: T[];
  totalTimeS: number;
  /** Sum of the known `effort_score`s only — see `loadComplete`. */
  totalLoad: number;
  /**
   * False when at least one activity this day has no `effort_score` yet (not
   * analysed): `totalLoad` above is a floor for the day, not the whole day's
   * load, and must not be presented as final.
   */
  loadComplete: boolean;
  /**
   * True only for the OLDEST group (last in the date-descending list) when
   * more history might still be loaded (`hasMore`). A day sitting at the page
   * boundary can never be presented as final: the runner's next "Load more"
   * tap might add another activity from the SAME day, growing both totals.
   */
  boundaryPartial: boolean;
}

// Mirrors the backend's `services/schedule/disciplines.discipline_for_activity_type`
// (#830), so a day mixing a run, a ride and a strength session colours each
// activity the way the schedule already does — no second colour language.
const DISCIPLINE_BY_TYPE: Record<string, Discipline> = {
  walk: 'walk',
  hike: 'walk',
  ride: 'bike',
  virtualride: 'bike',
  ebikeride: 'bike',
  mountainbikeride: 'bike',
  gravelride: 'bike',
  handcycle: 'bike',
  rowing: 'row',
  virtualrow: 'row',
  weighttraining: 'strength',
  crossfit: 'strength',
};

export function disciplineForActivityType(activityType: string): Discipline {
  const raw = (activityType || '').trim().toLowerCase();
  if (raw === 'run') return 'run';
  return DISCIPLINE_BY_TYPE[raw] ?? 'other';
}

export function groupActivitiesByDay<T extends GroupableActivity>(
  activities: T[],
  hasMore: boolean,
): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const indexByKey = new Map<string, number>();

  for (const activity of activities) {
    const date = activityStartDate(activity);
    const dayKey = format(date, 'yyyy-MM-dd');
    let idx = indexByKey.get(dayKey);
    if (idx === undefined) {
      idx = groups.length;
      indexByKey.set(dayKey, idx);
      groups.push({
        dayKey,
        date,
        activities: [],
        totalTimeS: 0,
        totalLoad: 0,
        loadComplete: true,
        boundaryPartial: false,
      });
    }
    const group = groups[idx];
    group.activities.push(activity);
    group.totalTimeS += activity.moving_time_s;
    if (activity.effort_score == null) {
      group.loadComplete = false;
    } else {
      group.totalLoad += activity.effort_score;
    }
  }

  // The incoming list is date-descending, so the LAST group is the oldest —
  // the only one a page boundary can cut, since pages are appended oldest-last.
  if (hasMore && groups.length > 0) {
    groups[groups.length - 1].boundaryPartial = true;
  }

  return groups;
}

/** "Today" / "Yesterday" / "Friday, Aug 21" (year added only when it is not
 * the current one), so a day header reads as a date the runner recognises
 * rather than an ISO string. */
export function dayHeading(date: Date): string {
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startOfDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startOfToday.getTime() - startOfDay.getTime()) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  const sameYear = date.getFullYear() === today.getFullYear();
  return format(date, sameYear ? 'EEEE, MMM d' : 'EEEE, MMM d, yyyy');
}
