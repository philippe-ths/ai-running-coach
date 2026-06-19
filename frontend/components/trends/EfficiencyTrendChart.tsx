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
import { ReactNode, useMemo, useState } from "react";
import ActivityTypeFilter from "./ActivityTypeFilter";
import ChartTooltip from "@/components/charts/ChartTooltip";

interface Props {
  data: EfficiencyPoint[];
  granularity: "daily" | "weekly";
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
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

export default function EfficiencyTrendChart({ data, granularity, delta }: Props) {
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);

  // Derive available types from the dataset
  const availableTypes = useMemo(() => {
    const types = new Set(data.map((p) => p.type));
    return Array.from(types).sort();
  }, [data]);

  const chartData = useMemo(() => {
    // 1. Filter data based on selection
    let filtered =
      selectedTypes.length === 0
        ? data
        : data.filter((p) => selectedTypes.includes(p.type));

    // 2. Sort chronologically to ensure trend line is correct
    filtered = [...filtered].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

    // 3. Calculate SMA on the filtered subset
    const sma = calculateSMA(filtered, 5);

    return filtered.map((p, idx) => ({
      ...p,
      label: formatDateLabel(p.date),
      // Convert m/s per bpm → meters per heartbeat (×60)
      value: +(p.efficiency_mps_per_bpm * 60).toFixed(2),
      [p.type]: +(p.efficiency_mps_per_bpm * 60).toFixed(2),
      // Trend line
      trend: sma[idx] != null ? +(sma[idx]! * 60).toFixed(2) : null,
    }));
  }, [data, selectedTypes]);

  // Extract unique types from chartData for coloring/legend
  const presentTypes = useMemo(() => {
    const types = new Set(chartData.map(p => p.type));
    return Array.from(types);
  }, [chartData]);
  
  const title = `Heart Rate Efficiency per ${granularity === "daily" ? "Day" : "Week"}`;

  // Color map for known types
  const getColor = (type: string) => {
    const t = type.toLowerCase();
    if (t === "run") return "#3b82f6";
    if (t === "walk") return "#f59e0b";
    if (t === "alpineski" || t === "ride") return "#8b5cf6";
    return "#64748b";
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">{title}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Meters per heartbeat. Higher is better.
          </p>
          {delta}
        </div>
        <div>
          <ActivityTypeFilter
            available={availableTypes}
            selected={selectedTypes}
            onChange={setSelectedTypes}
          />
        </div>
      </div>

      {chartData.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No sufficient heart rate data for this range.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
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
                width={50}
                domain={["auto", "auto"]}
                tickFormatter={(val) => `${val.toFixed(1)} m`}
                unit=""
              />
              <Tooltip
                content={
                  <ChartTooltip
                    formatter={(value, name) => [
                      typeof value === "number" ? `${value.toFixed(2)} m/beat` : (value as any),
                      name === "trend" ? "Trend (5-act avg)" : (name as any),
                    ]}
                  />
                }
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

              {/* Dynamic Scatter Lines per Type */}
              {presentTypes.map((type) => (
               <Line
                  key={type}
                  type="monotone"
                  dataKey={type}
                  stroke={getColor(type)}
                  strokeWidth={0}
                  dot={{ r: 3, fill: getColor(type) }}
                  connectNulls={false}
                  name={type}
                  isAnimationActive={false}
                />
              ))}

            </ComposedChart>
        </ResponsiveContainer>
      )}
      
      <div className="mt-3 text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-700/50 p-2 rounded">
        Efficiency is affected by heat, hills, wind, terrain, and stops. Compare similar routes/efforts for best signal.
      </div>
    </div>
  );
}
