'use client';

import React, { useMemo, useRef, useState } from 'react';
import { ActivityStream } from '@/lib/types';
import { Activity, Heart, Zap, Mountain, Gauge } from 'lucide-react';

interface StreamChartsProps {
  streams?: ActivityStream[];
}

const CHART_HEIGHT = 100;
const CHART_WIDTH = 600; // SVG internal coordinate space

const PRESETS: Record<string, { label: string, color: string, icon: any, format?: (v: number) => string }> = {
  heartrate: { label: 'Heart Rate', color: 'text-rose-500', icon: Heart, format: (v) => `${Math.round(v)} bpm` },
  velocity_smooth: {
    label: 'Pace',
    color: 'text-blue-500',
    icon: Gauge,
    format: (mps) => {
      if (mps <= 0.1) return '0:00 /km';
      const secPerKm = 1000 / mps;
      const min = Math.floor(secPerKm / 60);
      const sec = Math.round(secPerKm % 60);
      return `${min}:${sec.toString().padStart(2, '0')} /km`;
    }
  },
  cadence: { label: 'Cadence', color: 'text-purple-500', icon: Activity, format: (v) => `${Math.round(v)} spm` },
  smoothed_cadence: { label: 'Cadence', color: 'text-purple-500', icon: Activity, format: (v) => `${Math.round(v)} spm` },
  watts: { label: 'Power', color: 'text-amber-500', icon: Zap, format: (v) => `${Math.round(v)} W` },
  altitude: { label: 'Elevation', color: 'text-emerald-600', icon: Mountain, format: (v) => `${Math.round(v)} m` },
  grade_smooth: { label: 'Grade', color: 'text-gray-500', icon: Mountain, format: (v) => `${v.toFixed(1)}%` },
};

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

interface SimpleChartProps {
  type: string;
  data: (number | null)[];
  secondaryData?: (number | null)[];
  // Shared scrub position as a fraction [0, 1] of the run, synced across all
  // charts so the same moment is highlighted everywhere (#207).
  scrubFraction: number | null;
  onScrub: (fraction: number | null) => void;
  timeData?: (number | null)[];
}

// Memoized so a parent re-render that does not change this chart's props is a
// no-op (#368). The activity-detail page re-renders on unrelated state — most
// often the coach-report poll (#260) — and without memo every such tick re-ran
// all 4-6 charts. The data/secondaryData/timeData props are referentially
// stable (the same stream `.data` references) and onScrub is the stable
// useState setter, so only scrubFraction changes during a scrub; that fraction
// is shared, so all charts still update their synced indicator together (#207),
// which is intended.
const SimpleChart = React.memo(function SimpleChart({ type, data, secondaryData, scrubFraction, onScrub, timeData }: SimpleChartProps) {
  const preset = PRESETS[type] || { label: type, color: 'text-gray-500', icon: Activity };
  const Icon = preset.icon;
  const areaRef = useRef<HTMLDivElement>(null);

  // Compute stats for scaling using only valid data
  const { pathD, secondaryPathD, primaryData, min, max, avg, range } = useMemo(() => {
    // Cadence dropouts/ramps must not set the chart scale (#310). Strava reports
    // 0 cadence before the first detected step (and during pauses), and the
    // smoothing of that region emits LOW NON-ZERO ramp values (real data observed:
    // smoothed_cadence = [0, 0, 76, 152, ...]). Dropping only exact zeros leaves
    // the ramp (76) as the scale minimum, compressing the real ~150-180 spm signal
    // into a sliver with a dark empty band below. So gap any cadence sample below
    // half the median of real (>0) cadence: this catches both the zeros and the
    // ramp while keeping genuine low cadence. Cadence-type ONLY: 0 is a legitimate
    // value for altitude / grade / power, so this is never a blanket normalisation.
    const isCadence = type === 'cadence' || type === 'smoothed_cadence';
    const dropCadenceDropouts = (arr: (number | null)[]) => {
      const positives = arr.filter((v): v is number => v !== null && v > 0);
      if (!positives.length) return arr;
      const sorted = [...positives].sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      const floor = median * 0.5;
      return arr.map((v) => (v !== null && v < floor ? null : v));
    };
    const primaryData = isCadence ? dropCadenceDropouts(data) : data;
    const secondaryDataNormalized = secondaryData
      ? (isCadence ? dropCadenceDropouts(secondaryData) : secondaryData)
      : undefined;

    // Filter out nulls for stats
    const validData = primaryData.filter((v): v is number => v !== null);
    if (!validData.length) return { pathD: '', secondaryPathD: '', primaryData, min: 0, max: 0, avg: 0, range: 1 };

    // Include secondary data in min/max calc so it fits in the chart
    const allValues = [...validData];
    if (secondaryDataNormalized) {
        allValues.push(...secondaryDataNormalized.filter((v): v is number => v !== null));
    }

    let min = Infinity;
    let max = -Infinity;
    let sum = 0;

    for (const v of allValues) {
      if (v < min) min = v;
      if (v > max) max = v;
    }

    // Avg only from main data
    for (const v of validData) {
        sum += v;
    }

    const avg = sum / validData.length;
    const range = max - min || 1;

    // Helper to generate path with gaps
    const generatePath = (arr: (number | null)[]) => {
        let commands: string[] = [];
        for (let i = 0; i < arr.length; i++) {
            const val = arr[i];
            if (val === null) continue;

            const x = (i / (arr.length - 1)) * CHART_WIDTH;
            const y = CHART_HEIGHT - ((val - min) / range) * CHART_HEIGHT;

            // If previous point was null or this is first point, Move to. Else Line to.
            const prevVal = i > 0 ? arr[i-1] : null;
            if (prevVal === null || i === 0) {
                commands.push(`M ${x.toFixed(1)},${y.toFixed(1)}`);
            } else {
                commands.push(`L ${x.toFixed(1)},${y.toFixed(1)}`);
            }
        }
        return commands.join(' ');
    };

    const pathD = generatePath(primaryData);
    const secondaryPathD = secondaryDataNormalized ? generatePath(secondaryDataNormalized) : '';

    return {
      pathD,
      secondaryPathD,
      primaryData,
      min,
      max,
      avg,
      range
    };
  }, [data, secondaryData, type]);

  const validCount = data.filter(x => x !== null).length;
  if (validCount === 0) return null;

  // Hide charts with no meaningful data (flat zeros)
  if (Math.abs(max) < 0.0001 && Math.abs(min) < 0.0001) return null;

  const formatValue = preset.format || ((v: number) => Math.round(v).toString());

  const scrubIndex = scrubFraction === null
    ? null
    : Math.min(data.length - 1, Math.max(0, Math.round(scrubFraction * (data.length - 1))));
  const scrubValue = scrubIndex === null ? null : primaryData[scrubIndex];
  const scrubTime = scrubIndex === null || !timeData ? null : timeData[scrubIndex];
  const scrubX = scrubIndex === null ? null : (scrubIndex / (data.length - 1)) * CHART_WIDTH;
  const scrubY = scrubValue === null || scrubValue === undefined
    ? null
    : CHART_HEIGHT - ((scrubValue - min) / range) * CHART_HEIGHT;

  const handlePointer = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = areaRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const fraction = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    onScrub(fraction);
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex justify-between items-center mb-4">
        <h4 className={`font-semibold text-sm flex items-center gap-2 capitalize ${preset.color}`}>
          <Icon size={16} />
          {preset.label}
        </h4>
        {scrubIndex !== null ? (
          <div className="flex gap-3 text-xs font-mono">
            <span className={`font-semibold ${preset.color}`}>
              {scrubValue !== null && scrubValue !== undefined ? formatValue(scrubValue) : '–'}
            </span>
            {scrubTime !== null && scrubTime !== undefined && (
              <span className="text-gray-400 dark:text-gray-500">@ {formatElapsed(scrubTime)}</span>
            )}
          </div>
        ) : (
          <div className="flex gap-3 text-xs text-gray-400 dark:text-gray-500 font-mono">
             <span>Max: {formatValue(max)}</span>
             <span>Avg: {formatValue(avg)}</span>
          </div>
        )}
      </div>

      <div
        ref={areaRef}
        className="relative w-full h-24 cursor-crosshair"
        style={{ touchAction: 'pan-y' }}
        onPointerDown={handlePointer}
        onPointerMove={handlePointer}
        onPointerLeave={() => onScrub(null)}
      >
        <svg
          viewBox={`0 -5 ${CHART_WIDTH} ${CHART_HEIGHT + 10}`}
          className="w-full h-full overflow-visible"
          preserveAspectRatio="none"
        >
          {/* Secondary Line (Raw) */}
          {secondaryPathD && (
            <path
              d={secondaryPathD}
              fill="none"
              stroke="currentColor"
              className={`${preset.color} opacity-20`}
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          )}

          {/* Main Line */}
          <path
            d={pathD}
            fill="none"
            stroke="currentColor"
            className={preset.color}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />

          {/* Scrub indicator: vertical line + dot at the sampled value */}
          {scrubX !== null && (
            <g>
              <line
                x1={scrubX} x2={scrubX} y1={-5} y2={CHART_HEIGHT + 5}
                stroke="currentColor"
                className="text-gray-400 dark:text-gray-500"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
              {scrubY !== null && (
                <circle
                  cx={scrubX} cy={scrubY} r="3.5"
                  fill="currentColor"
                  className={preset.color}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </g>
          )}
        </svg>
      </div>
    </div>
  );
});

export default function StreamCharts({ streams }: StreamChartsProps) {
  // One scrub position shared by every chart, as a fraction of the run.
  const [scrubFraction, setScrubFraction] = useState<number | null>(null);

  if (!streams || streams.length === 0) return null;

  // Order of display
  const order = ['heartrate', 'velocity_smooth', 'altitude', 'cadence', 'smoothed_cadence', 'watts'];

  // Deduplicate streams by type to prevent rendering issues if backend sends duplicates
  const uniqueStreams = Array.from(new Map(streams.map(s => [s.stream_type, s])).values());

  const hasSmoothed = uniqueStreams.some(s => s.stream_type === 'smoothed_cadence');
  const timeData = uniqueStreams.find(s => s.stream_type === 'time')?.data;

  const processedStreams: ActivityStream[] = [];
  const secondaryMap: Record<string, (number | null)[]> = {};

  for (const s of uniqueStreams) {
      // If we have smoothed cadence, hide raw cadence but capture its data for secondary display
      if (s.stream_type === 'cadence' && hasSmoothed) {
          secondaryMap['smoothed_cadence'] = s.data;
          continue;
      }

      // Filter unknown types
      if (!PRESETS[s.stream_type]) continue;

      processedStreams.push(s);
  }

  const sortedStreams = processedStreams.sort((a, b) => {
        const idxA = order.indexOf(a.stream_type);
        const idxB = order.indexOf(b.stream_type);
        return (idxA === -1 ? 99 : idxA) - (idxB === -1 ? 99 : idxB);
  });

  return (
    <div className="grid grid-cols-1 gap-4">
       {sortedStreams.map((s) => (
         <SimpleChart
            key={s.stream_type}
            type={s.stream_type}
            data={s.data}
            secondaryData={secondaryMap[s.stream_type]}
            scrubFraction={scrubFraction}
            onScrub={setScrubFraction}
            timeData={timeData}
         />
       ))}
    </div>
  );
}
