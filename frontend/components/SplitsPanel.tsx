import React from 'react';
import { Split } from '@/lib/types/activity';
import { formatPace, formatDistanceKm, formatSplitDuration } from '@/lib/format';

interface SplitsPanelProps {
  splits?: Split[];
}

export function SplitsPanel({ splits }: SplitsPanelProps) {
  if (!splits || splits.length === 0) {
    return null;
  }

  const isTimeBased = splits[0]?.split_type === 'time';
  const hasPower = splits.some((s) => s.avg_watts != null);
  const hasGrade = splits.some((s) => s.avg_grade != null);
  const hasElevGain = splits.some((s) => s.elev_gain != null);

  return (
    <div className="bg-white dark:bg-gray-800 shadow rounded-lg p-6 mb-6">
      <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-4">
        {isTimeBased ? 'Splits (5 min)' : 'Splits (1 km)'}
      </h3>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          {/* Header style is kept identical to LapsPanel's thead/th so the two
              activity tables read as one UI (#293). */}
          <thead className="bg-gray-100 dark:bg-gray-700">
            <tr>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                {isTimeBased ? '#' : 'Km'}
              </th>
              {!isTimeBased && (
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Distance
                </th>
              )}
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                {isTimeBased ? 'Duration' : 'Pace'}
              </th>
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Avg HR
              </th>
              {hasGrade && (
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Avg Grade
                </th>
              )}
              {hasElevGain && (
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Elev Gain
                </th>
              )}
              {hasPower && (
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Avg Power
                </th>
              )}
              <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Avg Cadence
              </th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {splits.map((split) => (
              <tr key={split.split}>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {split.split}
                </td>
                {!isTimeBased && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split.distance != null ? formatDistanceKm(split.distance) : '-'}
                  </td>
                )}
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {isTimeBased
                    ? formatSplitDuration(split.elapsed_time)
                    : split.distance != null
                      ? formatPace(split.distance, split.elapsed_time)
                      : '-'}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {split.avg_hr ? Math.round(split.avg_hr) : '-'}
                </td>
                {hasGrade && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split.avg_grade != null ? `${split.avg_grade.toFixed(1)}%` : '-'}
                  </td>
                )}
                {hasElevGain && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split.elev_gain != null ? `${split.elev_gain} m` : '-'}
                  </td>
                )}
                {hasPower && (
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split.avg_watts != null ? `${Math.round(split.avg_watts)} W` : '-'}
                  </td>
                )}
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                  {split.avg_cadence ? Math.round(split.avg_cadence) : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
