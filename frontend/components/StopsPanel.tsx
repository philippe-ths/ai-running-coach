import { StopsAnalysis } from '@/lib/types/metrics';
import { formatDuration, formatDistanceKm } from '@/lib/format';

function formatStopDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  return formatDuration(seconds);
}

export default function StopsPanel({ stopsData }: { stopsData?: StopsAnalysis }) {
  if (!stopsData || stopsData.stopped_count === 0) {
    return null;
  }

  const { total_stopped_time_s, stopped_count, longest_stop_s, stops } = stopsData;

  return (
    <div className="bg-white p-6 rounded-lg shadow-sm border border-gray-200">
      <h3 className="text-lg font-semibold text-gray-900 mb-4">Stop / Idle Analysis</h3>
      
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-gray-50 p-4 rounded-md">
          <p className="text-sm text-gray-500 mb-1">Total Stopped Time</p>
          <p className="text-xl font-medium text-gray-900">{formatStopDuration(total_stopped_time_s)}</p>
        </div>
        <div className="bg-gray-50 p-4 rounded-md">
          <p className="text-sm text-gray-500 mb-1"># of Stops</p>
          <p className="text-xl font-medium text-gray-900">{stopped_count}</p>
        </div>
        <div className="bg-gray-50 p-4 rounded-md">
          <p className="text-sm text-gray-500 mb-1">Longest Stop</p>
          <p className="text-xl font-medium text-gray-900">{formatStopDuration(longest_stop_s)}</p>
        </div>
      </div>

      <h4 className="text-sm font-medium text-gray-700 mb-3">Stop Locations</h4>
      <div className="space-y-3">
        {stops.map((stop, i) => (
            <div key={i} className="flex justify-between items-center bg-gray-50 p-3 rounded text-sm">
                <div className="flex flex-col">
                    <span className="font-medium text-gray-900">Stop #{i + 1}</span>
                    <span className="text-gray-500">
                        at {stop.distance_m ? formatDistanceKm(stop.distance_m) : 'Start/Unknown'}
                    </span>
                </div>
                <div className="text-right">
                    <span className="font-mono text-gray-700">{formatStopDuration(stop.duration_s)}</span>
                    {stop.location && (
                        <div className="text-xs text-gray-400 mt-1">
                            {stop.location[0].toFixed(5)}, {stop.location[1].toFixed(5)}
                        </div>
                    )}
                </div>
            </div>
        ))}
      </div>
    </div>
  );
}
