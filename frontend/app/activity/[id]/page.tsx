import { fetchFromAPI } from '@/lib/api';
import { format } from 'date-fns';
import { formatPace, formatDuration, formatDistanceKm } from '@/lib/format';
import CheckInForm from '@/components/CheckInForm';
import Link from 'next/link';
import { Activity } from '@/lib/types';
import AdvancedMetrics from '@/components/AdvancedMetrics';
import StreamCharts from '@/components/StreamCharts';
import { SplitsPanel } from '@/components/SplitsPanel';
import StopsPanel from '@/components/StopsPanel';
import EfficiencyPanel from '@/components/EfficiencyPanel';
import CoachReportPanel from '@/components/CoachReportPanel';

export const dynamic = 'force-dynamic';

export default async function ActivityDetail({ params }: { params: { id: string } }) {
  const activity: Activity | null = await fetchFromAPI(`/api/activities/${params.id}`);
  
  if (!activity) return <div>Activity not found</div>;

  return (
    <div className="space-y-6 relative">

      <div className="mb-4">
        <Link href="/" className="text-blue-600 hover:underline text-sm">← Back to Dashboard</Link>
      </div>

      <header className="border-b pb-4">
        <div className="flex justify-between items-start">
            <div>
                <h1 className="text-3xl font-bold text-gray-900">{activity.name}</h1>
                <div className="flex gap-4 mt-2 text-gray-600">
                    <span>{format(new Date(activity.start_date), 'PPPP p')}</span>
                    <span>{formatDistanceKm(activity.distance_m)}</span>
                </div>
            </div>
            {/* IntentSelector moved to main content */}
        </div>
      </header>

      <div className="grid md:grid-cols-3 gap-6">
        
        {/* Main Content */}
        <div className="md:col-span-2 space-y-6">

          {/* Activity Context Panel: Check-In & Type */}
          <CheckInForm 
              activityId={activity.id} 
              existingCheckIn={activity.check_in} 
              currentType={activity.user_intent ?? null}
              assignedClass={activity.metrics?.activity_class}
              sportType={activity.raw_summary?.sport_type || activity.raw_summary?.type || 'Run'}
          />
          
          {/* Coach Analysis */}
          <CoachReportPanel activityId={activity.id} hasMetrics={!!activity.metrics} />

          {/* Detailed Stream Charts */}
          {activity.streams && activity.streams.length > 0 && (
             <StreamCharts streams={activity.streams} />
          )}

          {/* Splits Panel */}
          {activity.splits && activity.splits.length > 0 && (
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
              <StopsPanel stopsData={activity.metrics.stops_analysis} />
          )}

        </div>

        {/* Sidebar: Check-In & Stats */}
        <div className="space-y-6">
           <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
              <h3 className="font-semibold text-gray-700 mb-3">Metrics</h3>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                    <dt className="text-gray-500">Duration</dt>
                    <dd className="font-medium">{formatDuration(activity.moving_time_s)}</dd>
                </div>
                {activity.raw_summary?.elapsed_time && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Elapsed Time</dt>
                        <dd className="font-medium">{formatDuration(activity.raw_summary.elapsed_time)}</dd>
                    </div>
                )}
                <div className="flex justify-between">
                   <dt className="text-gray-500">Avg Pace</dt>
                   <dd className="font-medium">
                     {formatPace(activity.distance_m, activity.moving_time_s)}
                   </dd>
                </div>
                <div className="flex justify-between">
                    <dt className="text-gray-500">Distance</dt>
                    <dd className="font-medium">{formatDistanceKm(activity.distance_m)}</dd>
                </div>
                
                <div className="border-t border-gray-100 my-2 pt-2"></div>
                
                <div className="flex justify-between">
                    <dt className="text-gray-500">Avg HR</dt>
                    <dd className="font-medium">{activity.avg_hr ? `${Math.round(activity.avg_hr)} bpm` : '-'}</dd>
                </div>
                {activity.raw_summary?.max_heartrate && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Max HR</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.max_heartrate)} bpm</dd>
                    </div>
                )}
                {activity.raw_summary?.suffer_score && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Suffer Score</dt>
                        <dd className="font-medium">{activity.raw_summary.suffer_score}</dd>
                    </div>
                )}

                {(activity.raw_summary?.average_watts || activity.raw_summary?.kilojoules) && (
                    <div className="border-t border-gray-100 my-2 pt-2"></div>
                )}

                {activity.raw_summary?.average_watts && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Avg Power</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.average_watts)} W</dd>
                    </div>
                )}
                {activity.raw_summary?.weighted_average_watts && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Norm. Power</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.weighted_average_watts)} W</dd>
                    </div>
                )}
                {activity.raw_summary?.kilojoules && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Energy</dt>
                        <dd className="font-medium">{Math.round(activity.raw_summary.kilojoules)} kJ</dd>
                    </div>
                )}

                <div className="border-t border-gray-100 my-2 pt-2"></div>
                
                {activity.avg_cadence && (
                    <>
                        <div className="flex justify-between">
                            <dt className="text-gray-500">Avg Cadence</dt>
                            <dd className="font-medium">{Math.round(activity.avg_cadence)} spm</dd>
                        </div>
                        <div className="border-t border-gray-100 my-2 pt-2"></div>
                    </>
                )}
                <div className="flex justify-between">
                    <dt className="text-gray-500">Elevation</dt>
                    <dd className="font-medium">{Math.round(activity.elev_gain_m)} m</dd>
                </div>
                {activity.raw_summary?.device_name && (
                    <div className="flex justify-between">
                        <dt className="text-gray-500">Device</dt>
                        <dd className="font-medium text-right max-w-[150px] truncate" title={activity.raw_summary.device_name}>
                            {activity.raw_summary.device_name}
                        </dd>
                    </div>
                )}
              </dl>
           </div>
        </div>
      </div>

      {/* Debug Section */}
      <details className="mt-8 text-xs text-slate-400">
        <summary className="cursor-pointer mb-2">Debug: Raw Strava Data & Streams</summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
                <h4 className="font-semibold mb-2">Strava Activity Summary</h4>
                <pre className="p-4 bg-slate-50 rounded overflow-x-auto border border-slate-100 h-96">
                {JSON.stringify(activity.raw_summary, null, 2)}
                </pre>
            </div>
            <div>
                 <h4 className="font-semibold mb-2">Hidden Streams (High Frequency Data)</h4>
                 {activity.streams && activity.streams.length > 0 ? (
                     <pre className="p-4 bg-slate-50 rounded overflow-x-auto border border-slate-100 h-96">
                        {JSON.stringify(activity.streams, null, 2)}
                     </pre>
                 ) : (
                     <div className="p-4 bg-slate-50 rounded border border-slate-100 h-96 flex items-center justify-center italic text-slate-500">
                         No streams available (or not loaded).
                     </div>
                 )}
            </div>
        </div>
      </details>

    </div>
  );
}
