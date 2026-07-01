import Link from 'next/link';
import { serverFetch } from '@/lib/serverSession';
import { WeeklyStatsData } from '@/lib/types';
import { formatDistanceKm, formatDuration } from '@/lib/format';
import ActivityList from '@/components/ActivityList';
import SyncButton from '@/components/SyncButton';
import SelfHealTrigger from '@/components/SelfHealTrigger';
import StatDiff from '@/components/StatDiff';
import StravaConnectNotice from '@/components/StravaConnectNotice';
import TelegramLinkPrompt from '@/components/TelegramLinkPrompt';

// Force dynamic since we fetch user data (no static cache for dashboard)
export const dynamic = 'force-dynamic';

/**
 * Percentage change in load vs the previous 7 days, e.g. "+12%".
 * Returns null when there's no prior load to compare against (can't divide by 0).
 */
function formatLoadTrend(current: number, previous: number): string | null {
  if (previous <= 0) return null;
  const pct = Math.round(((current - previous) / previous) * 100);
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct}%`;
}

export default async function Dashboard() {
  // Recent activity list (for the feed) and the weekly summary stats are
  // separate concerns. The summary aggregates over a true rolling 7-day window
  // server-side (matching Trends 7D) instead of summing a capped recent list,
  // so the two surfaces report identical numbers (#246).
  let activities = [];
  let stats: WeeklyStatsData | null = null;
  try {
    [activities, stats] = await Promise.all([
      serverFetch('/api/activities?limit=10').then((a) => a || []),
      serverFetch('/api/stats/weekly') as Promise<WeeklyStatsData | null>,
    ]);
  } catch (e) {
    console.error('Failed to fetch dashboard data', e);
  }

  const summary = stats?.summary;
  const previous = stats?.previous_summary;
  const loadTrend =
    summary && previous
      ? formatLoadTrend(summary.total_load, previous.total_load)
      : null;

  return (
    <div className="space-y-8">
      <StravaConnectNotice />
      <TelegramLinkPrompt />
      <header className="flex flex-col gap-4 sm:flex-row sm:justify-between sm:items-start">
        <div>
            <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Weekly Summary</h1>
            <p className="text-gray-600 dark:text-gray-400 mt-1">Here is what is happening this week.</p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
            <SelfHealTrigger />
            <SyncButton />
        </div>
      </header>

      {/* Week stats — rolling 7-day window vs the previous 7 days (#246, #248). */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
            <div className="text-sm text-gray-500 dark:text-gray-400">Distance</div>
            <div className="text-2xl font-bold">
              {summary ? formatDistanceKm(summary.total_distance_m) : '—'}
            </div>
            {summary && previous && (
              <StatDiff
                current={summary.total_distance_m}
                previous={previous.total_distance_m}
                format={formatDistanceKm}
              />
            )}
        </div>
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
            <div className="text-sm text-gray-500 dark:text-gray-400">Time</div>
            <div className="text-2xl font-bold">
              {summary ? formatDuration(summary.total_moving_time_s) : '—'}
            </div>
            {summary && previous && (
              <StatDiff
                current={summary.total_moving_time_s}
                previous={previous.total_moving_time_s}
                format={formatDuration}
              />
            )}
        </div>
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
            <div className="text-sm text-gray-500 dark:text-gray-400">Hard Days</div>
            <div className="text-2xl font-bold">{summary ? summary.hard_days : '—'}</div>
            {summary && previous && (
              <StatDiff
                current={summary.hard_days}
                previous={previous.hard_days}
                format={(v) => v.toString()}
              />
            )}
        </div>
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
            <div className="text-sm text-gray-500 dark:text-gray-400">Load Trend</div>
            <div className="text-2xl font-bold">{loadTrend ?? '--%'}</div>
            {summary && previous && (
              <StatDiff
                current={summary.total_load}
                previous={previous.total_load}
                format={(v) => Math.round(v).toLocaleString()}
              />
            )}
        </div>
      </div>

      <section>
        <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-800 dark:text-gray-200">Recent Activities</h2>
            <Link
              href="/activities"
              className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300"
            >
              See all
            </Link>
        </div>
        <ActivityList activities={activities} />
      </section>
    </div>
  );
}
