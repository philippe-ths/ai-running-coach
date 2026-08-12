import { serverFetch } from '@/lib/serverSession';
import { format } from 'date-fns';
import { formatPace, formatDuration, formatDistanceKm, activityStartDate, hasMeaningfulDistance } from '@/lib/format';
import CheckInForm from '@/components/CheckInForm';
import Link from 'next/link';
import { Activity } from '@/lib/types';
import AdvancedMetrics from '@/components/AdvancedMetrics';
import RoutePath from '@/components/RoutePath';
import StreamCharts from '@/components/StreamCharts';
import { SplitsPanel } from '@/components/SplitsPanel';
import { LapsPanel } from '@/components/LapsPanel';
import { lapsAreAutoDistance } from '@/lib/laps';
import StopsPanel from '@/components/StopsPanel';
import FeatureDisabledGate from '@/components/FeatureDisabledGate';
import EfficiencyPanel from '@/components/EfficiencyPanel';
import CoachSection from '@/components/CoachSection';
import TrainingLoadCard from '@/components/TrainingLoadCard';
import TelegramLinkPrompt from '@/components/TelegramLinkPrompt';

export const dynamic = 'force-dynamic';

// Dev-only raw-data/streams dump. Inlined at build time; set
// NEXT_PUBLIC_SHOW_DEBUG_PANEL=true (or "1") to expose it. Off in production so
// the full stream arrays do not ship in every activity page's SSR HTML (#359).
const SHOW_DEBUG_PANEL =
  process.env.NEXT_PUBLIC_SHOW_DEBUG_PANEL === 'true' ||
  process.env.NEXT_PUBLIC_SHOW_DEBUG_PANEL === '1';

export default async function ActivityDetail({ params }: { params: { id: string } }) {
  const activity: Activity | null = await serverFetch(`/api/activities/${params.id}`);

  if (!activity) return <div>Activity not found</div>;

  // #522: coach input feature flags drive UI greying (fail open to enabled).
  const coachFlags = (await serverFetch('/api/coach/feature-flags')) || {};
  const stopsEnabled = coachFlags.stops_analysis !== false;

  // #562: when the recorded laps are just per-km auto-distance laps, they
  // duplicate the per-km splits, so render one unified Laps view (enriched
  // with the splits' richer columns) instead of two near-identical cards.
  // Device-meaningful laps (manual button / structured workout) keep both.
  const hasLaps = !!activity.laps && activity.laps.length > 0;
  const autoDistanceLaps = lapsAreAutoDistance(activity.laps, activity.splits);
  const showSplitsPanel =
    !!activity.splits && activity.splits.length > 0 && !(hasLaps && autoDistanceLaps);

  return (
    <div className="space-y-6 relative">

      <div className="mb-4">
        <Link href="/" className="text-blue-600 dark:text-blue-400 hover:underline text-sm">← Back to Dashboard</Link>
      </div>

      <TelegramLinkPrompt />

      <header className="border-b dark:border-gray-700 pb-4">
        <div className="flex justify-between items-start">
            <div className="min-w-0">
                <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100 break-words">{activity.name}</h1>
                <div className="flex flex-wrap gap-x-4 gap-y-2 mt-2 text-gray-600 dark:text-gray-400 items-center">
                    <span>{format(activityStartDate(activity), 'PPPP p')}</span>
                    {hasMeaningfulDistance(activity.distance_m) && (
                        <span>{formatDistanceKm(activity.distance_m)}</span>
                    )}
                    {activity.metrics?.headline && (
                        <span className="inline-block px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 text-sm font-medium">
                            {activity.metrics.headline}
                        </span>
                    )}
                </div>
            </div>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

        {/* Main Content */}
        <div className="md:col-span-2 space-y-6 min-w-0">

          {/* Route shape traced from the recorded GPS track (no basemap, #408).
              Renders nothing for activities without a latlng stream. */}
          {activity.streams && activity.streams.length > 0 && (
             <RoutePath streams={activity.streams} />
          )}

          {/* Activity Context Panel: Check-In & Type */}
          <CheckInForm 
              activityId={activity.id} 
              existingCheckIn={activity.check_in} 
              currentType={activity.user_intent ?? null}
              headline={activity.metrics?.headline}
              typeOptions={activity.intent_options}
          />
          
          {/* Training Load: current-condition read as of this activity (#276) */}
          {activity.training_load && (
              <TrainingLoadCard data={activity.training_load} />
          )}

          {/* Coach Analysis + follow-up chat. The report's conversational
              question options start the chat below (chat-as-continuation). */}
          <CoachSection activityId={activity.id} hasMetrics={!!activity.metrics} />


          {/* Detailed Stream Charts */}
          {activity.streams && activity.streams.length > 0 && (
             <StreamCharts streams={activity.streams} />
          )}

          {/* Laps Panel: only when the runner recorded laps (#208). When the
              laps are just per-km auto-distance laps, the aligned splits are
              passed so this single view carries their richer columns (#562). */}
          {hasLaps && (
              <LapsPanel
                laps={activity.laps}
                splits={autoDistanceLaps ? activity.splits : undefined}
              />
          )}

          {/* Splits Panel: hidden when it would duplicate auto-distance laps (#562) */}
          {showSplitsPanel && (
              <SplitsPanel splits={activity.splits} />
          )}

          {/* Advanced Metrics Visualization */}
          {activity.metrics && (
             <AdvancedMetrics metrics={activity.metrics} />
          )}

          {/* Efficiency Analysis */}
          {activity.metrics?.efficiency_analysis && (
              <EfficiencyPanel data={activity.metrics.efficiency_analysis} />
          )}

          {/* Stops Analysis */}
          {activity.metrics?.stops_analysis && (
              <FeatureDisabledGate
                disabled={!stopsEnabled}
                note="Stop / idle analysis is turned off in the coach configuration; the coach does not read it."
              >
                <StopsPanel stopsData={activity.metrics.stops_analysis} />
              </FeatureDisabledGate>
          )}

        </div>

        {/* Sidebar: Check-In & Stats */}
        <div className="space-y-6 min-w-0">
           <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="font-semibold text-gray-700 dark:text-gray-300 mb-3">Metrics</h3>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                    <dt className="text-gray-500 dark:text-gray-400">Duration</dt>
                    <dd className="font-medium">{formatDuration(activity.moving_time_s)}</dd>
                </div>
                {activity.raw_summary?.elapsed_time && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Elapsed Time</dt>
                        <dd className="font-medium">{formatDuration(activity.raw_summary.elapsed_time)}</dd>
                    </div>
                )}
                {hasMeaningfulDistance(activity.distance_m) && (
                  <>
                    <div className="flex justify-between">
                       <dt className="text-gray-500 dark:text-gray-400">Avg Pace</dt>
                       <dd className="font-medium">
                         {formatPace(activity.distance_m, activity.moving_time_s)}
                       </dd>
                    </div>
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Distance</dt>
                        <dd className="font-medium">{formatDistanceKm(activity.distance_m)}</dd>
                    </div>
                  </>
                )}

                <div className="border-t border-gray-100 dark:border-gray-700 my-2 pt-2"></div>
                
                <div className="flex justify-between">
                    <dt className="text-gray-500 dark:text-gray-400">Avg HR</dt>
                    <dd className="font-medium">{activity.avg_hr ? `${Math.round(activity.avg_hr)} bpm` : '-'}</dd>
                </div>
                {activity.raw_summary?.max_heartrate && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Max HR</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.max_heartrate)} bpm</dd>
                    </div>
                )}
                {activity.raw_summary?.suffer_score && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Strava Suffer Score</dt>
                        <dd className="font-medium">{activity.raw_summary.suffer_score}</dd>
                    </div>
                )}

                {(activity.raw_summary?.average_watts || activity.raw_summary?.kilojoules) && (
                    <div className="border-t border-gray-100 dark:border-gray-700 my-2 pt-2"></div>
                )}

                {activity.raw_summary?.average_watts && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Avg Power</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.average_watts)} W</dd>
                    </div>
                )}
                {activity.raw_summary?.weighted_average_watts && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Norm. Power</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.weighted_average_watts)} W</dd>
                    </div>
                )}
                {activity.raw_summary?.kilojoules && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Energy</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.kilojoules)} kJ</dd>
                    </div>
                )}

                <div className="border-t border-gray-100 dark:border-gray-700 my-2 pt-2"></div>
                
                {activity.avg_cadence && (
                    <>
                        <div className="flex justify-between">
                            <dt className="text-gray-500 dark:text-gray-400">Avg Cadence</dt>
                            <dd className="font-medium">{Math.round(activity.avg_cadence)} spm</dd>
                        </div>
                        <div className="border-t border-gray-100 dark:border-gray-700 my-2 pt-2"></div>
                    </>
                )}
                <div className="flex justify-between">
                    <dt className="text-gray-500 dark:text-gray-400">Elevation</dt>
                    <dd className="font-medium">{Math.round(activity.elev_gain_m)} m</dd>
                </div>
                {activity.raw_summary?.device_name && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500 dark:text-gray-400">Device</dt>
                        <dd className="font-medium text-right max-w-[150px] truncate" title={activity.raw_summary.device_name}>
                            {activity.raw_summary.device_name}
                        </dd>
                    </div>
                )}
              </dl>
           </div>
        </div>
      </div>

      {/* Debug Section (dev only; gated by NEXT_PUBLIC_SHOW_DEBUG_PANEL, #359) */}
      {SHOW_DEBUG_PANEL && (
      <details className="mt-8 text-xs text-slate-400 dark:text-gray-500">
        <summary className="cursor-pointer mb-2">Debug: Raw Strava Data & Streams</summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
                <h4 className="font-semibold mb-2">Strava Activity Summary</h4>
                <pre className="p-4 bg-slate-50 dark:bg-gray-700/50 rounded overflow-x-auto border border-slate-100 dark:border-gray-700 h-96">
                {JSON.stringify(activity.raw_summary, null, 2)}
                </pre>
            </div>
            <div>
                 <h4 className="font-semibold mb-2">Hidden Streams (High Frequency Data)</h4>
                 {activity.streams && activity.streams.length > 0 ? (
                     <pre className="p-4 bg-slate-50 dark:bg-gray-700/50 rounded overflow-x-auto border border-slate-100 dark:border-gray-700 h-96">
                        {JSON.stringify(activity.streams, null, 2)}
                     </pre>
                 ) : (
                     <div className="p-4 bg-slate-50 dark:bg-gray-700/50 rounded border border-slate-100 dark:border-gray-700 h-96 flex items-center justify-center italic text-slate-500 dark:text-gray-400">
                         No streams available (or not loaded).
                     </div>
                 )}
            </div>
        </div>
      </details>
      )}

    </div>
  );
}
