'use client';

import React, { useState } from 'react';
import { Lap, Split } from '@/lib/types/activity';
import { formatSplitDuration } from '@/lib/format';

interface LapsPanelProps {
  laps?: Lap[];
  // When the recorded laps are just per-km auto-distance laps, the page passes
  // the aligned per-km splits so this single view can carry the richer per-row
  // columns the splits table shows (grade, elevation, power) without losing
  // data, instead of rendering a near-identical second card (#562). Aligned by
  // index; the laps remain the row basis and bar-chart source.
  splits?: Split[];
}

type Metric = 'pace' | 'hr';

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

function lapHasPace(lap: Lap): boolean {
  return lap.avg_speed_mps != null && lap.avg_speed_mps > 0.1;
}

function lapHasHr(lap: Lap): boolean {
  return lap.avg_hr != null && lap.avg_hr > 0;
}

// Value that drives bar height for the chosen metric. For pace we use speed so
// faster laps stand out (taller = quicker); for HR we use the raw bpm.
function lapMetricValue(lap: Lap, metric: Metric): number | null {
  if (metric === 'pace') return lapHasPace(lap) ? lap.avg_speed_mps! : null;
  return lapHasHr(lap) ? lap.avg_hr! : null;
}

export function LapsPanel({ laps, splits }: LapsPanelProps) {
  const hasPace = !!laps?.some(lapHasPace);
  const hasHr = !!laps?.some(lapHasHr);
  // Default to pace when available, otherwise fall back to HR.
  const [metric, setMetric] = useState<Metric>(hasPace ? 'pace' : 'hr');
  const [selected, setSelected] = useState<number | null>(null);

  if (!laps || laps.length === 0) {
    return null;
  }

  // If the preferred metric has no data, show whatever we do have.
  const activeMetric: Metric = metric === 'pace' && !hasPace ? 'hr' : metric === 'hr' && !hasHr ? 'pace' : metric;
  const showToggle = hasPace && hasHr;

  // Scale bars across the value range so differences are visible. HR values sit
  // in a narrow band, so anchoring to the min (not zero) keeps the chart useful.
  const values = laps.map((l) => lapMetricValue(l, activeMetric)).filter((v): v is number => v != null);
  const minValue = values.length ? Math.min(...values) : 0;
  const maxValue = values.length ? Math.max(...values) : 1;
  const range = maxValue - minValue;

  const barHeightPct = (lap: Lap): number => {
    const v = lapMetricValue(lap, activeMetric);
    if (v == null) return 0;
    if (range <= 0) return 100;
    // Floor of 12% keeps even the smallest bar visible/clickable.
    return 12 + ((v - minValue) / range) * 88;
  };

  const barColor = (isSelected: boolean): string => {
    if (activeMetric === 'hr') {
      return isSelected
        ? 'bg-rose-600'
        : 'bg-rose-300 dark:bg-rose-800/80 group-hover:bg-rose-400 dark:group-hover:bg-rose-700';
    }
    return isSelected
      ? 'bg-blue-600'
      : 'bg-blue-300 dark:bg-blue-800 group-hover:bg-blue-400 dark:group-hover:bg-blue-700';
  };

  const barTitle = (lap: Lap): string =>
    activeMetric === 'hr'
      ? `Lap ${lap.lap}: ${lap.avg_hr ? Math.round(lap.avg_hr) + ' bpm' : '-'}`
      : `Lap ${lap.lap}: ${formatLapPace(lap)}`;

  // Enrichment columns sourced from the aligned per-km split (#562). Only the
  // unified auto-distance case passes splits; each column appears only when
  // some row has a value, mirroring SplitsPanel so no data is lost when the
  // separate splits card is collapsed away. Cadence prefers the lap's own.
  const splitAt = (i: number): Split | undefined => splits?.[i];
  const cadenceFor = (lap: Lap, i: number): number | null =>
    lap.avg_cadence ?? splitAt(i)?.avg_cadence ?? null;
  const showCadence =
    laps.some((l) => l.avg_cadence != null) || !!splits?.some((s) => s.avg_cadence != null);
  const showGrade = !!splits?.some((s) => s.avg_grade != null);
  const showElev = !!splits?.some((s) => s.elev_gain != null);
  const showPower = !!splits?.some((s) => s.avg_watts != null);

  const columns = [
    'Lap',
    'Distance',
    'Time',
    'Pace',
    'Avg HR',
    ...(showGrade ? ['Avg Grade'] : []),
    ...(showElev ? ['Elev Gain'] : []),
    ...(showPower ? ['Avg Power'] : []),
    ...(showCadence ? ['Avg Cadence'] : []),
  ];

  return (
    <div className="bg-white dark:bg-gray-800 shadow rounded-lg p-6 mb-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100">Laps</h3>

        {showToggle && (
          <div className="flex items-center rounded-full border border-gray-200 dark:border-gray-700 p-0.5 text-xs font-medium">
            {(['pace', 'hr'] as const).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={activeMetric === m}
                onClick={() => setMetric(m)}
                className={`px-3 py-1 rounded-full transition-colors ${
                  activeMetric === m
                    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'
                    : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
              >
                {m === 'pace' ? 'Pace' : 'HR'}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Per-lap bar chart for the selected metric */}
      <div
        className="flex items-end gap-1 h-28 mb-6"
        role="img"
        aria-label={activeMetric === 'hr' ? 'Average heart rate per lap' : 'Pace per lap'}
      >
        {laps.map((lap) => {
          const isSelected = selected === lap.lap;
          return (
            <button
              key={lap.lap}
              type="button"
              onClick={() => setSelected(isSelected ? null : lap.lap)}
              className="flex-1 flex items-end h-full group"
              title={barTitle(lap)}
            >
              <div
                className={`w-full rounded-t transition-colors ${barColor(isSelected)}`}
                style={{ height: `${barHeightPct(lap)}%` }}
              />
            </button>
          );
        })}
      </div>

      <div className="overflow-x-auto max-h-80 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-700">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          {/* Header style is kept identical to SplitsPanel's thead/th so the two
              activity tables read as one UI (#293). The sticky positioning sits
              below the NavBar (z-30) so the header never paints over it. */}
          <thead className="sticky top-0 z-10 bg-gray-100 dark:bg-gray-700">
            <tr>
              {columns.map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {laps.map((lap, i) => {
              const split = splitAt(i);
              return (
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
                {showGrade && (
                  <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split?.avg_grade != null ? `${split.avg_grade.toFixed(1)}%` : '-'}
                  </td>
                )}
                {showElev && (
                  <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split?.elev_gain != null ? `${split.elev_gain} m` : '-'}
                  </td>
                )}
                {showPower && (
                  <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {split?.avg_watts != null ? `${Math.round(split.avg_watts)} W` : '-'}
                  </td>
                )}
                {showCadence && (
                  <td className="px-6 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                    {cadenceFor(lap, i) != null ? Math.round(cadenceFor(lap, i)!) : '-'}
                  </td>
                )}
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
