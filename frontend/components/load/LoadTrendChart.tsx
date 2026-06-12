"use client";

import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { LoadWeek } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";
import ChartTooltip from "@/components/charts/ChartTooltip";

interface Props {
  weeks: LoadWeek[];
  selectedWeekStart?: string;
}

export default function LoadTrendChart({ weeks }: Props) {
  const chartData = weeks.map((w) => ({
    label: formatDateLabel(w.week_start),
    score: w.score,
    // Range area for the optimal band; null when the week has no baseline.
    band:
      w.target_min != null && w.target_max != null
        ? [w.target_min, w.target_max]
        : null,
  }));

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">
        Weekly Load Trend
      </h3>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-4">
        Weekly load against your optimal range (0.8–1.3× your trailing 4-week average).
      </p>
      {chartData.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No load history yet.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 12 }} tickLine={false} axisLine={false} />
            <YAxis tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={40} />
            <Tooltip
              content={
                <ChartTooltip
                  formatter={(value, name) => {
                    if (name === "band" && Array.isArray(value)) {
                      return [`${value[0]} – ${value[1]}`, "Optimal range"];
                    }
                    return [(value as number) ?? 0, "Weekly load"];
                  }}
                />
              }
            />
            <Area
              dataKey="band"
              stroke="none"
              fill="#22c55e"
              fillOpacity={0.15}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              dataKey="score"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={{ r: 2 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
