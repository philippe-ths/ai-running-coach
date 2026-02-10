'use client';

import React, { useMemo } from 'react';
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

function SimpleChart({ type, data, secondaryData }: { type: string, data: (number | null)[], secondaryData?: (number | null)[] }) {
  const preset = PRESETS[type] || { label: type, color: 'text-gray-500', icon: Activity };
  const Icon = preset.icon;

  // Compute stats for scaling using only valid data
  const { pathD, secondaryPathD, min, max, avg } = useMemo(() => {
    // Filter out nulls for stats
    const validData = data.filter((v): v is number => v !== null);
    if (!validData.length) return { pathD: '', secondaryPathD: '', min: 0, max: 0, avg: 0 };
    
    // Include secondary data in min/max calc so it fits in the chart
    const allValues = [...validData];
    if (secondaryData) {
        allValues.push(...secondaryData.filter((v): v is number => v !== null));
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

    const pathD = generatePath(data);
    const secondaryPathD = secondaryData ? generatePath(secondaryData) : '';

    return { 
      pathD,
      secondaryPathD,
      min,
      max, 
      avg 
    };
  }, [data, secondaryData]);

  const validCount = data.filter(x => x !== null).length;
  if (validCount === 0) return null;
  
  // Hide charts with no meaningful data (flat zeros)
  if (Math.abs(max) < 0.0001 && Math.abs(min) < 0.0001) return null;

  const formatValue = preset.format || ((v: number) => Math.round(v).toString());

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
      <div className="flex justify-between items-center mb-4">
        <h4 className={`font-semibold text-sm flex items-center gap-2 capitalize ${preset.color}`}>
          <Icon size={16} />
          {preset.label}
        </h4>
        <div className="flex gap-3 text-xs text-gray-400 font-mono">
           <span>Max: {formatValue(max)}</span>
           <span>Avg: {formatValue(avg)}</span>
        </div>
      </div>
      
      <div className="relative w-full h-24">
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

          {/* Background Area (Optional, implies fill - complex with gaps so skipping fill for now, user asked for line breaks) */}
          
          {/* Main Line */}
          <path 
            d={pathD} 
            fill="none" 
            stroke="currentColor" 
            className={preset.color} 
            strokeWidth="2" 
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
    </div>
  );
}

export default function StreamCharts({ streams }: StreamChartsProps) {
  if (!streams || streams.length === 0) return null;

  // Order of display
  const order = ['heartrate', 'velocity_smooth', 'altitude', 'cadence', 'smoothed_cadence', 'watts'];
  
  // Deduplicate streams by type to prevent rendering issues if backend sends duplicates
  const uniqueStreams = Array.from(new Map(streams.map(s => [s.stream_type, s])).values());

  const hasSmoothed = uniqueStreams.some(s => s.stream_type === 'smoothed_cadence');
  
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
         />
       ))}
    </div>
  );
}
