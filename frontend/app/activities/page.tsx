'use client';

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import ActivityList from '@/components/ActivityList';

// One request per "page". The backend GET /api/activities already returns
// activities newest-first with skip/limit pagination, so this view just walks it.
const PAGE_SIZE = 20;

interface ActivityListItem {
  id: string;
  name: string;
  type: string;
  start_date: string;
  distance_m: number;
  moving_time_s: number;
  headline?: string | null;
}

export default function AllActivitiesPage() {
  const [activities, setActivities] = useState<ActivityListItem[]>([]);
  const [status, setStatus] = useState<'loading' | 'loaded' | 'loadingMore' | 'error'>('loading');
  const [hasMore, setHasMore] = useState(true);

  const loadFrom = useCallback(async (skip: number) => {
    const isFirstPage = skip === 0;
    setStatus(isFirstPage ? 'loading' : 'loadingMore');
    try {
      const batch: ActivityListItem[] | null = await fetchFromAPI(
        `/api/activities?skip=${skip}&limit=${PAGE_SIZE}`,
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

  useEffect(() => {
    loadFrom(0);
  }, [loadFrom]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">All Activities</h1>
        <p className="text-gray-600 dark:text-gray-400 mt-1">Your full run history, newest first.</p>
      </header>

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
            onClick={() => loadFrom(0)}
            className="mt-2 text-sm text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-200 underline"
          >
            Try again
          </button>
        </div>
      )}

      {status !== 'loading' && activities.length > 0 && (
        <ActivityList activities={activities} />
      )}

      {status === 'loaded' && activities.length === 0 && (
        <div className="text-gray-500 dark:text-gray-400 italic">
          No activities found yet. Try syncing from the home page.
        </div>
      )}

      {hasMore && activities.length > 0 && (
        <div className="flex justify-center">
          <button
            onClick={() => loadFrom(activities.length)}
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
