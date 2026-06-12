'use client';

import React, { useState } from 'react';
import { Lap } from '@/lib/types/activity';
import { formatSplitDuration } from '@/lib/format';

interface LapsPanelProps {
  laps?: Lap[];
}

function lapPaceSecPerKm(lap: Lap): number | null {
  if (lap.avg_speed_mps && lap.avg_speed_mps > 0.1) return 1000 / lap.avg_speed_mps;
  return null;
}

function formatLapPace(lap: Lap): string {
  const secPerKm = lapPaceSecPerKm(lap);
  if (secPerKm === null) return '-';
  const min = Math.floor(secPerKm / 60);
  const sec = Math.round(secPerKm % 60);
  return `${min}:${sec.toString().padStart(2, '0')} /km`;
}

function formatLapDistance(lap: Lap): string {
  if (lap.distance_m == null) return '-';
  return `${(lap.distance_m / 1000).toFixed(2)} km`;
}

export function LapsPanel({ laps }: LapsPanelProps) {
  const [selected, setSelected] = useState<number | null>(null);

  if (!laps || laps.length === 0) {
    return null;
  }

  // Bar height scales with speed so faster laps stand out (Strava-style).
  const speeds = laps.map((l) => l.avg_speed_mps ?? 0);
  const maxSpeed = Math.max(...speeds, 0.1);

  return (
    <div className="bg-white dark:bg-gray-800 shadow rounded-lg p-6 mb-6">
      <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-4">Laps</h3>

      {/* Pace-per-lap bar chart */}
      <div className="flex items-end gap-1 h-28 mb-6" role="img" aria-label="Pace per lap">
        {laps.map((lap, i) => {
          const heightPct = Math.max(((lap.avg_speed_mps ?? 0) / maxSpeed) * 100, 4);
          const isSelected = selected === lap.lap;
          return (
            <button
              key={lap.lap}
              type="button"
              onClick={() => setSelected(isSelected ? null : lap.lap)}
              className="flex-1 flex items-end h-full group"
              title={`Lap ${lap.lap}: ${formatLapPace(lap)}`}
            >
              <div
                className={`w-full rounded-t transition-colors ${
                  isSelected ? 'bg-blue-600' : 'bg-blue-300 dark:bg-blue-800 group-hover:bg-blue-400 dark:group-hover:bg-blue-700'
                }`}
                style={{ height: `${heightPct}%` }}
              />
            </button>
          );
        })}
      </div>

      <div className="overflow-x-auto max-h-80 overflow-y-auto">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-700/50 sticky top-0">
            <tr>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Lap
              </th>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Distance
              </th>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Time
              </th>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Pace
              </th>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Avg HR
              </th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {laps.map((lap) => (
              <tr
                key={lap.lap}
                onClick={() => setSelected(selected === lap.lap ? null : lap.lap)}
                className={`cursor-pointer ${selected === lap.lap ? 'bg-blue-50 dark:bg-blue-900/30' : 'hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
              >
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">{lap.lap}</td>
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">{formatLapDistance(lap)}</td>
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {lap.elapsed_time_s != null ? formatSplitDuration(lap.elapsed_time_s) : '-'}
                </td>
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">{formatLapPace(lap)}</td>
                <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {lap.avg_hr ? Math.round(lap.avg_hr) : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
