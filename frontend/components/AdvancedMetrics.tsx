import { DerivedMetric } from '@/lib/types';
import { BarChart, Activity, Heart } from 'lucide-react';

interface Props {
  metrics: DerivedMetric;
}

export default function AdvancedMetrics({ metrics }: Props) {
  // Check if we have adv metrics
  const hasDrift = metrics.hr_drift !== undefined && metrics.hr_drift !== null;
  const hasZones = metrics.time_in_zones && Object.keys(metrics.time_in_zones).length > 0;
  
  // Calculate total time for zone percentages
  const totalTime = metrics.time_in_zones 
    ? Object.values(metrics.time_in_zones).reduce((a, b) => a + b, 0) 
    : 0;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
      <h2 className="text-xl font-semibold mb-4 text-gray-800 dark:text-gray-200 flex items-center gap-2">
        <Activity className="w-5 h-5 text-blue-600 dark:text-blue-400" />
        Advanced Analysis
      </h2>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left Col: Drift & Pace Var */}
        <div className="space-y-4">
            <h3 className="font-medium text-gray-700 dark:text-gray-300">Efficiency & Stability</h3>

            {hasDrift ? (
                <div className="bg-slate-50 dark:bg-gray-700/50 p-4 rounded-lg">
                    <div className="flex justify-between items-center mb-1">
                        <span className="text-sm text-gray-600 dark:text-gray-400">Cardiac Drift</span>
                        <span className={`font-bold ${metrics.hr_drift! > 5 ? 'text-red-500' : 'text-green-600'}`}>
                            {metrics.hr_drift}%
                        </span>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                        {metrics.hr_drift! > 5
                            ? "High decoupling. You worked harder for the same pace later in the run (fatigue/hydration)."
                            : "Good aerobic durability. HR stayed stable relative to pace."
                        }
                    </p>
                </div>
            ) : (
                <div className="text-xs text-gray-400 dark:text-gray-500 italic">Not enough data for Cardiac Drift.</div>
            )}

            <div className="bg-slate-50 dark:bg-gray-700/50 p-4 rounded-lg">
                <div className="flex justify-between items-center mb-1">
                    <span className="text-sm text-gray-600 dark:text-gray-400">Pace Variability</span>
                    <span className="font-bold text-slate-700 dark:text-gray-300">
                        {metrics.pace_variability != null ? `${metrics.pace_variability}%` : '—'}
                    </span>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                    Coefficient of Variation. Lower means a steadier, more consistent effort.
                </p>
            </div>
        </div>

        {/* Right Col: Zones */}
        <div className="space-y-4">
             <h3 className="font-medium text-gray-700 dark:text-gray-300">Time in Zones</h3>
             {hasZones && metrics.time_in_zones ? (
                 <div className="space-y-2">
                     {Object.entries(metrics.time_in_zones).map(([zone, seconds]) => {
                         const pct = totalTime > 0 ? (seconds / totalTime) * 100 : 0;
                         // Color mapping for zones
                         const colors: Record<string, string> = {
                             "Z1": "bg-gray-300",
                             "Z2": "bg-blue-400",
                             "Z3": "bg-green-500",
                             "Z4": "bg-orange-500",
                             "Z5": "bg-red-600"
                         };
                         
                         return (
                            <div key={zone} className="flex items-center gap-2 text-xs">
                                <span className="w-6 font-medium text-gray-600 dark:text-gray-400">{zone}</span>
                                <div className="flex-1 h-3 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                                    <div 
                                        className={`h-full ${colors[zone] || 'bg-blue-500'}`} 
                                        style={{ width: `${pct}%` }}
                                    ></div>
                                </div>
                                <span className="w-10 text-right text-gray-500 dark:text-gray-400">{Math.round(pct)}%</span>
                            </div>
                         );
                     })}
                     <p className="text-xs text-gray-400 dark:text-gray-500 mt-2 text-right">
                         {metrics.hr_zones_source === 'strava'
                           ? 'Based on your Strava HR zones'
                           : 'Based on % of max HR (default 190 bpm if not set)'}
                     </p>
                 </div>
             ) : (
                 <div className="text-xs text-gray-400 dark:text-gray-500 italic">No heart rate data available for zone analysis.</div>
             )}
        </div>

      </div>
    </div>
  );
}
