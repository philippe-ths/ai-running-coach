'use client';

import React, { useMemo } from 'react';
import { ActivityStream } from '@/lib/types';
import { Activity, Heart, Zap, Mountain, Gauge } from 'lucide-react';

interface StreamChartsProps {
  streams?: ActivityStream[];
}

const CHART_HEIGHT = 100;
const CHART_WIDTH = 600; // SVG internal coordinate space

const PRESETS: Record<string, { label: string, color: string, icon: any }> = {
  heartrate: { label: 'Heart Rate', color: 'text-rose-500', icon: Heart },
  velocity_smooth: { label: 'Pace (Velocity)', color: 'text-blue-500', icon: Gauge },
  cadence: { label: 'Cadence', color: 'text-purple-500', icon: Activity },
  watts: { label: 'Power', color: 'text-amber-500', icon: Zap },
  altitude: { label: 'Elevation', color: 'text-emerald-600', icon: Mountain },
  grade_smooth: { label: 'Grade', color: 'text-gray-500', icon: Mountain },
};

function SimpleChart({ type, data }: { type: string, data: number[] }) {
  const preset = PRESETS[type] || { label: type, color: 'text-gray-500', icon: Activity };
  const Icon = preset.icon;

  // Compute stats for scaling
  const { pathD, min, max, avg } = useMemo(() => {
    if (!data.length) return { pathD: '', min: 0, max: 0, avg: 0 };
    
    let min = Infinity;
    let max = -Infinity;
    let sum = 0;

    for (const v of data) {
      if (v < min) min = v;
      if (v > max) max = v;
      sum += v;
    }
    const avg = Math.round(sum / data.length);
    const range = max - min || 1;

    // Generate Path
    // x = (index / length) * WIDTH
    // y = HEIGHT - ((value - min) / range) * HEIGHT
    const points = data.map((val, i) => {
      const x = (i / (data.length - 1)) * CHART_WIDTH;
      const y = CHART_HEIGHT - ((val - min) / range) * CHART_HEIGHT;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    return { 
      pathD: `M ${points.join(' L ')}`,
      min: Math.floor(min),
      max: Math.ceil(max), 
      avg 
    };
  }, [data]);

  if (data.length === 0) return null;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
      <div className="flex justify-between items-center mb-4">
        <h4 className={`font-semibold text-sm flex items-center gap-2 capitalize ${preset.color}`}>
          <Icon size={16} />
          {preset.label}
        </h4>
        <div className="flex gap-3 text-xs text-gray-400 font-mono">
           <span>Max: {max}</span>
           <span>Avg: {avg}</span>
        </div>
      </div>
      
      <div className="relative w-full h-24">
        <svg 
          viewBox={`0 -5 ${CHART_WIDTH} ${CHART_HEIGHT + 10}`} 
          className="w-full h-full overflow-visible"
          preserveAspectRatio="none"
        >
          {/* Background Area (Optional, implies fill) */}
          <path 
            d={`${pathD} L ${CHART_WIDTH},${CHART_HEIGHT} L 0,${CHART_HEIGHT} Z`} 
            fill="currentColor" 
            className={`${preset.color} opacity-10`} 
            stroke="none"
          />
          {/* Line */}
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
  const order = ['heartrate', 'velocity_smooth', 'altitude', 'cadence', 'watts'];
  
  // Sort streams by preferred order
  const sortedStreams = [...streams]
    .filter(s => PRESETS[s.stream_type]) // Only show known graphical types
    .sort((a, b) => {
        const idxA = order.indexOf(a.stream_type);
        const idxB = order.indexOf(b.stream_type);
        // If not in preferred list, push to end
        return (idxA === -1 ? 99 : idxA) - (idxB === -1 ? 99 : idxB);
    });

  return (
    <div className="grid grid-cols-1 gap-4">
       {sortedStreams.map((s) => (
         <SimpleChart key={s.stream_type} type={s.stream_type} data={s.data} />
       ))}
    </div>
  );
}
