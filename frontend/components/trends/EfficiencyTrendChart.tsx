"use client";

import {
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { EfficiencyPoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";
import { useMemo } from "react";

interface Props {
  data: EfficiencyPoint[];
}

function calculateSMA(data: EfficiencyPoint[], window: number = 5): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - window + 1);
    const subset = data.slice(start, i + 1);
    const sum = subset.reduce((acc, curr) => acc + curr.efficiency_mps_per_bpm, 0);
    result.push(sum / subset.length);
  }
  return result;
}

export default function EfficiencyTrendChart({ data }: Props) {
  const chartData = useMemo(() => {
    const sma = calculateSMA(data, 5); // 5-activity moving average
    return data.map((p, idx) => ({
      ...p,
      label: formatDateLabel(p.date),
      // Scatter points
      run: p.type.toLowerCase() === "run" ? p.efficiency_mps_per_bpm : undefined,
      walk: p.type.toLowerCase() === "walk" ? p.efficiency_mps_per_bpm : undefined,
      // Trend line
      trend: sma[idx],
    }));
  }, [data]);

  const hasRun = data.some((p) => p.type.toLowerCase() === "run");
  const hasWalk = data.some((p) => p.type.toLowerCase() === "walk");

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-700">
          Heart Rate Efficiency
        </h3>
        <p className="text-xs text-gray-500 mt-1">
          Speed (m/s) per Heart beat. Higher is better.
        </p>
      </div>

      {chartData.length === 0 ? (
        <p className="text-gray-400 text-sm py-8 text-center">
          No sufficient heart rate data for this range.
        </p>
      ) : (
        <div className="h-[300px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                width={40}
                domain={["auto", "auto"]}
                tickFormatter={(val) => val.toFixed(3)}
              />
              <Tooltip
                formatter={(value: number, name: string) => [
                  value.toFixed(4),
                  name === "trend" ? "Trend (5-act avg)" : name,
                ]}
                labelFormatter={(label) => label}
              />
              <Legend />
              
              {/* Trend Line */}
              <Line
                type="monotone"
                dataKey="trend"
                stroke="#64748b" // slate-500
                strokeWidth={2}
                dot={false}
                name="Trend"
              />

              {/* Individual Activities as Scatter points (using Line with only dots and no line connection if we want specific coloring) 
                 Actually ComposedChart allows Scatter. But combining Line and Scatter is tricky with categorical axis sometimes. 
                 Alternative: Use Line with strokeWidth={0} for dots.
              */}
              
               {hasRun && (
                <Line
                  type="monotone"
                  dataKey="run"
                  stroke="#3b82f6"
                  strokeWidth={0}
                  dot={{ r: 3, fill: "#3b82f6" }}
                  connectNulls={false}
                  name="Run"
                  isAnimationActive={false}
                />
              )}
              {hasWalk && (
                <Line
                  type="monotone"
                  dataKey="walk"
                  stroke="#f59e0b"
                  strokeWidth={0}
                  dot={{ r: 3, fill: "#f59e0b" }}
                  connectNulls={false}
                  name="Walk"
                  isAnimationActive={false}
                />
              )}

            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      
      <div className="mt-3 text-xs text-gray-500 bg-gray-50 p-2 rounded">
        Efficiency is affected by heat, hills, wind, terrain, and stops. Compare similar routes/efforts for best signal.
      </div>
    </div>
  );
}
