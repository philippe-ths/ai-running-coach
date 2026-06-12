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
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center gap-2 mb-4">
         <div className="p-2 bg-emerald-100 dark:bg-emerald-900/30 rounded-full text-emerald-700 dark:text-emerald-300">
            <Zap size={18} />
         </div>
         <h3 className="font-semibold text-gray-800 dark:text-gray-200">Efficiency Analysis</h3>
      </div>
      
      <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="bg-gray-50 dark:bg-gray-700/50 p-3 rounded-md border border-gray-100 dark:border-gray-700">
              <div className="text-xs text-gray-500 dark:text-gray-400 uppercase font-medium">Avg Efficiency</div>
              <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">{fmt(average)}</div>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">m/min per bpm</div>
          </div>
          <div className="bg-emerald-50 dark:bg-emerald-900/30 p-3 rounded-md border border-emerald-100 dark:border-emerald-800">
              <div className="text-xs text-emerald-700 dark:text-emerald-300 uppercase font-medium">Best 3 min</div>
              <div className="text-2xl font-bold text-emerald-700 dark:text-emerald-300">{fmt(best_sustained)}</div>
              <div className="text-xs text-emerald-600 dark:text-emerald-400 mt-1">Sustained</div>
          </div>
      </div>
      
      <div className="relative h-24 w-full bg-gray-50/50 dark:bg-gray-700/30 rounded-lg p-2 border border-gray-100 dark:border-gray-700">
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
       <p className="text-xs text-gray-400 dark:text-gray-500 text-center mt-2">
           Rolling Efficiency (Speed/HR)
       </p>
    </div>
  );
}
