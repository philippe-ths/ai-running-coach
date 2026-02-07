import { fetchFromAPI } from '@/lib/api';
import { format } from 'date-fns';
import CheckInForm from '@/components/CheckInForm';
import IntentSelector from '@/components/IntentSelector';
import Link from 'next/link';
import Markdown from 'react-markdown';
import { Sparkles } from 'lucide-react';
import ChatPanel from '@/components/ChatPanel';
import { Activity, CoachVerdictV3 } from '@/lib/types'; // Updated import
import AdvancedMetrics from '@/components/AdvancedMetrics';
import CoachReport from '@/components/CoachReport';
import CoachVerdictV3Display from '@/components/CoachVerdictV3Display';
import VerdictV3Fetcher from '@/components/VerdictV3Fetcher'; // New import
import StreamCharts from '@/components/StreamCharts';
import { isCoachVerdictV3 } from '@/lib/feature_flags';
import { FEATURE_FLAGS } from '@/lib/feature_flags';
import { stableStringify } from '@/lib/stable_stringify';
import { createHash } from 'crypto';

// Force dynamic for detail view to ensure fresh advice
export const dynamic = 'force-dynamic';

export default async function ActivityDetail({ params }: { params: { id: string } }) {
  const activity: Activity | null = await fetchFromAPI(`/api/activities/${params.id}`);

  // Fetch advice if joined, or separately. 
  // Assuming backend returns it on the activity object or we'd fetch `/api/advice/${id}` in a real app.
  // The current backend models setup includes relationship loading.
  
  if (!activity) return <div>Activity not found</div>;

  // Check if we already have V3 compliant data
  const existingV3: CoachVerdictV3 | null = 
    activity.advice?.structured_report && isCoachVerdictV3(activity.advice.structured_report) 
    ? (activity.advice.structured_report as CoachVerdictV3) 
    : null;

  // Deterministic inputs fingerprint: only changes when intent or check-in changes.
  // We hash it to keep localStorage keys small/stable.
  const verdictInputsFingerprint = stableStringify({
    activity_id: activity.id,
    user_intent: activity.user_intent ?? null,
    check_in: activity.check_in ?? null,
  });
  const verdictInputsKey = createHash('sha256').update(verdictInputsFingerprint).digest('hex');

  return (
    <div className="space-y-6 relative pb-20"> 
       {/* pb-20 added to prevent content hiding behind fixed chat button on mobile/default */}

      <div className="mb-4">
        <Link href="/" className="text-blue-600 hover:underline text-sm">← Back to Dashboard</Link>
      </div>

      <header className="border-b pb-4">
        <div className="flex justify-between items-start">
            <div>
                <h1 className="text-3xl font-bold text-gray-900">{activity.name}</h1>
                <div className="flex gap-4 mt-2 text-gray-600">
                    <span>{format(new Date(activity.start_date), 'PPPP p')}</span>
                    <span>{(activity.distance_m / 1000).toFixed(2)} km</span>
                </div>
            </div>
            {/* Intent Selector: Allows overriding the classification */}
            <div className="flex flex-col items-end gap-1">
                 <IntentSelector 
                    activityId={activity.id} 
                    currentType={activity.user_intent ?? null}
                    assignedClass={activity.metrics?.activity_class} 
                 />
                 <span className="text-xs text-gray-500">
                    {activity.user_intent ? '(Manual override)' : '(Auto-detected)'}
                 </span>
            </div>
        </div>
      </header>

      <div className="grid md:grid-cols-3 gap-6">
        
        {/* Main Content: Advice */}
        <div className="md:col-span-2 space-y-6">
          <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
            <h2 className="text-xl font-semibold mb-4 text-gray-800 flex items-center gap-2">
              Coach Verdict {FEATURE_FLAGS.VERDICT_V3 && <span className="text-xs text-blue-500 font-normal uppercase border px-1 rounded">V3 Beta</span>}
            </h2>
            
              <VerdictV3Fetcher activityId={activity.id} inputsKey={verdictInputsKey} existingVerdict={existingV3}>
                {/* Fallback Legacy Content */}
                {activity.advice ? (
                     activity.advice.structured_report && !isCoachVerdictV3(activity.advice.structured_report) ? (
                          <CoachReport advice={activity.advice} /> 
                     ) : (
                          // Fallback removed: No full_text rendering
                          <div className="p-4 bg-yellow-50 text-yellow-800 rounded border border-yellow-200">
                              Analysis format not supported or pending upgrade.
                          </div>
                     )
                ) : (
                    <div className="prose prose-blue max-w-none text-gray-700">
                        <p className="italic text-gray-500">
                            Analysis pending or not included in current response schema.
                        </p>
                        <p>Sync your Strava activities and re-open this run to generate coaching.</p>
                    </div>
                )}
            </VerdictV3Fetcher>
            
          </div>
          
          {/* Detailed Stream Charts */}
          {activity.streams && activity.streams.length > 0 && (
             <StreamCharts streams={activity.streams} />
          )}

          {/* Advanced Metrics Visualization */}
          {activity.metrics && (
             <AdvancedMetrics metrics={activity.metrics} />
          )}

        </div>

        {/* Sidebar: Check-In & Stats */}
        <div className="space-y-6">
           {/* ... STATS SECTION UNCHANGED ... */}
           <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
              <h3 className="font-semibold text-gray-700 mb-3">Metrics</h3>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                    <dt className="text-gray-500">Duration</dt>
                    <dd className="font-medium">{Math.floor(activity.moving_time_s / 60)} min</dd>
                </div>
                <div className="flex justify-between">
                   <dt className="text-gray-500">Avg Pace</dt>
                   <dd className="font-medium">
                     {(() => {
                        const mps = activity.distance_m / activity.moving_time_s;
                        if (!mps) return '-';
                        const secPerKm = 1000 / mps;
                        const min = Math.floor(secPerKm / 60);
                        const sec = Math.round(secPerKm % 60);
                        return `${min}:${sec.toString().padStart(2, '0')} /km`;
                     })()}
                   </dd>
                </div>
                <div className="flex justify-between">
                    <dt className="text-gray-500">Distance</dt>
                    <dd className="font-medium">{(activity.distance_m / 1000).toFixed(2)} km</dd>
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
                <div className="border-t border-gray-100 my-2 pt-2"></div>
                <div className="flex justify-between">
                    <dt className="text-gray-500">Elevation</dt>
                    <dd className="font-medium">{Math.round(activity.elev_gain_m)} m</dd>
                </div>
              </dl>
           </div>
            
           {/* Check-in Widget (Read & Write handled internally) */}
           <CheckInForm 
              activityId={activity.id} 
              existingCheckIn={activity.check_in} 
           />
        </div>
      </div>

      {/* Chat Component - Floating */}
      <ChatPanel activityId={activity.id} />

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
