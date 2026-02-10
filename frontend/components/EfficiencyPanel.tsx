'use client';
import { EfficiencyAnalysis } from '@/lib/types/metrics';
import { TrendingUp, Zap } from 'lucide-react';

export default function EfficiencyPanel({ data }: { data: EfficiencyAnalysis }) {
  if (!data || !data.curve || !data.curve.length) return null;

  const { average, best_sustained, curve } = data;
  
  // Format numbers
  const fmt = (n: number) => n.toFixed(2);
  
  // Chart Logic
  const height = 60;
  const width = 100; // viewBox units
  const max = Math.max(...curve, best_sustained * 1.1);
  const min = Math.min(...curve) * 0.9;
  const range = max - min || 1;
  
  const points = curve.map((v, i) => {
    const x = (i / (curve.length - 1)) * width;
    const y = height - ((v - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-4">
         <div className="p-2 bg-emerald-100 rounded-full text-emerald-700">
            <Zap size={18} />
         </div>
         <h3 className="font-semibold text-gray-800">Efficiency Analysis</h3>
      </div>
      
      <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="bg-gray-50 p-3 rounded-md border border-gray-100">
              <div className="text-xs text-gray-500 uppercase font-medium">Avg Efficiency</div>
              <div className="text-2xl font-bold text-gray-900">{fmt(average)}</div>
              <div className="text-xs text-gray-400 mt-1">m/min per bpm</div>
          </div>
          <div className="bg-emerald-50 p-3 rounded-md border border-emerald-100">
              <div className="text-xs text-emerald-700 uppercase font-medium">Best 3 min</div>
              <div className="text-2xl font-bold text-emerald-700">{fmt(best_sustained)}</div>
              <div className="text-xs text-emerald-600 mt-1">Sustained</div>
          </div>
      </div>
      
      <div className="relative h-24 w-full bg-gray-50/50 rounded-lg p-2 border border-gray-100">
         <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full overflow-visible" preserveAspectRatio="none">
             {/* Gradient definition */}
             <defs>
                <linearGradient id="effGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity="0.2" />
                    <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
                </linearGradient>
            </defs>
            
            <path 
                d={`M0,${height} ${points} L${width},${height} Z`} 
                fill="url(#effGradient)" 
            />
            <polyline 
                points={points} 
                fill="none" 
                stroke="#10b981" 
                strokeWidth="2" 
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
         </svg>
      </div>
       <p className="text-xs text-gray-400 text-center mt-2">
           Rolling Efficiency (Speed/HR)
       </p>
    </div>
  );
}
