'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Loader2, NotebookPen, X } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import ActivityList from '@/components/ActivityList';
import ActivityTypeFilter from '@/components/trends/ActivityTypeFilter';
import DateRangeFilter, { DateRange } from '@/components/activities/DateRangeFilter';

// One request per "page". The backend GET /api/activities returns activities
// newest-first with skip/limit pagination and applies any filters server-side,
// so this view walks the *filtered* history a page at a time.
const PAGE_SIZE = 20;

interface ActivityListItem {
  id: string;
  name: string;
  type: string;
  start_date: string;
  start_date_local?: string | null;
  distance_m: number;
  moving_time_s: number;
  headline?: string | null;
  // #947: DerivedMetric.effort_score, null until analysed — the day-grouped
  // list's LOAD total.
  effort_score?: number | null;
}

interface Filters {
  types: string[];
  startDate: string | null;
  endDate: string | null;
}

const EMPTY_FILTERS: Filters = { types: [], startDate: null, endDate: null };

function filtersActive(f: Filters): boolean {
  return f.types.length > 0 || f.startDate !== null || f.endDate !== null;
}

function buildQuery(skip: number, f: Filters): string {
  const params = new URLSearchParams();
  params.set('skip', String(skip));
  params.set('limit', String(PAGE_SIZE));
  f.types.forEach((t) => params.append('types', t));
  if (f.startDate) params.set('start_date', f.startDate);
  if (f.endDate) params.set('end_date', f.endDate);
  return params.toString();
}

export default function AllActivitiesPage() {
  const [activities, setActivities] = useState<ActivityListItem[]>([]);
  const [status, setStatus] = useState<'loading' | 'loaded' | 'loadingMore' | 'error'>('loading');
  const [hasMore, setHasMore] = useState(true);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  // The floor the date-range stepper (#948) clamps "previous" against, fetched
  // once rather than per step; null until loaded or with no history.
  const [earliestActivityDate, setEarliestActivityDate] = useState<string | null>(null);

  // The distinct activity types present in the history, for the type filter.
  // Reuses the Trends endpoint (#404), which returns non-deleted types sorted.
  useEffect(() => {
    fetchFromAPI('/api/trends/types')
      .then((types: string[] | null) => setAvailableTypes(types ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchFromAPI('/api/activities/earliest-date')
      .then((r: { earliest_activity_date: string | null } | null) =>
        setEarliestActivityDate(r?.earliest_activity_date ?? null),
      )
      .catch(() => {});
  }, []);

  // `activeFilters` is passed explicitly so pagination never reads a stale closure.
  const loadFrom = useCallback(async (skip: number, activeFilters: Filters) => {
    const isFirstPage = skip === 0;
    setStatus(isFirstPage ? 'loading' : 'loadingMore');
    try {
      const batch: ActivityListItem[] | null = await fetchFromAPI(
        `/api/activities?${buildQuery(skip, activeFilters)}`,
      );
      const items = batch ?? [];
      setActivities((prev) => (isFirstPage ? items : [...prev, ...items]));
      // A short page means we've reached the end; a full page implies more.
      setHasMore(items.length === PAGE_SIZE);
      setStatus('loaded');
    } catch {
      setStatus('error');
    }
  }, []);

  // Reload from the first page whenever the filters change (and on mount). The
  // server reapplies the filters to the whole history, so the list stays correct
  // rather than narrowing only the rows already loaded.
  useEffect(() => {
    loadFrom(0, filters);
  }, [filters, loadFrom]);

  const isFiltered = filtersActive(filters);
  const clearFilters = () => setFilters(EMPTY_FILTERS);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">All Activities</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-1">Your full run history, newest first.</p>
        </div>
        {/* #946: the entry point to a coach review over a chosen stretch, rather
            than one activity at a time. */}
        <Link
          href="/period-reports"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
        >
          <NotebookPen className="w-4 h-4" aria-hidden="true" />
          Ask for a period review
        </Link>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <ActivityTypeFilter
          available={availableTypes}
          selected={filters.types}
          onChange={(types) => setFilters((f) => ({ ...f, types }))}
        />
        <DateRangeFilter
          startDate={filters.startDate}
          endDate={filters.endDate}
          onChange={(r: DateRange) =>
            setFilters((f) => ({ ...f, startDate: r.startDate, endDate: r.endDate }))
          }
          earliestActivityDate={earliestActivityDate}
        />
        {isFiltered && (
          <button
            onClick={clearFilters}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-sm font-medium rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
            Clear filters
          </button>
        )}
      </div>

      {status === 'loading' && (
        <div className="flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 py-10">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span>Loading activities…</span>
        </div>
      )}

      {status === 'error' && activities.length === 0 && (
        <div className="bg-red-50 dark:bg-red-900/30 rounded-xl border border-red-200 dark:border-red-800 p-6">
          <p className="text-red-700 dark:text-red-300 text-sm">Could not load activities.</p>
          <button
            onClick={() => loadFrom(0, filters)}
            className="mt-2 text-sm text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-200 underline"
          >
            Try again
          </button>
        </div>
      )}

      {status !== 'loading' && activities.length > 0 && (
        // #947: hasMore tells the list which day (only ever the oldest, since
        // the list is date-descending) cannot yet claim a final total — the
        // next "Load more" tap might still add to it.
        <ActivityList activities={activities} hasMore={hasMore} />
      )}

      {status === 'loaded' && activities.length === 0 && (
        isFiltered ? (
          <div className="text-gray-500 dark:text-gray-400">
            No activities match these filters.{' '}
            <button
              onClick={clearFilters}
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              Clear filters
            </button>{' '}
            to see your full history.
          </div>
        ) : (
          <div className="text-gray-500 dark:text-gray-400 italic">
            No activities found yet. Try syncing from the home page.
          </div>
        )
      )}

      {hasMore && activities.length > 0 && (
        <div className="flex justify-center">
          <button
            onClick={() => loadFrom(activities.length, filters)}
            disabled={status === 'loadingMore'}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-5 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-200 hover:border-blue-300 dark:hover:border-blue-700 hover:text-blue-600 dark:hover:text-blue-400 transition-colors disabled:opacity-60 disabled:cursor-default"
          >
            {status === 'loadingMore' ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Loading…
              </>
            ) : (
              'Load more'
            )}
          </button>
        </div>
      )}
    </div>
  );
}
