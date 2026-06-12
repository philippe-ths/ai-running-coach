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

interface Props {
  weeks: LoadWeek[];
  selectedWeekStart?: string;
}

export default function LoadTrendChart({ weeks }: Props) {
  // Per-week optimal range; null for early weeks that lack a trailing baseline.
  const rawBands = weeks.map((w) =>
    w.target_min != null && w.target_max != null
      ? ([w.target_min, w.target_max] as [number, number])
      : null,
  );
  // Carry the nearest known range into bandless weeks so the optimal band spans
  // the whole chart instead of only the recent weeks that have a baseline.
  const firstKnown = rawBands.find((b) => b != null) ?? null;
  let lastKnown = firstKnown;
  const filledBands = rawBands.map((b) => {
    if (b != null) lastKnown = b;
    return b ?? lastKnown;
  });

  const chartData = weeks.map((w, i) => ({
    label: formatDateLabel(w.week_start),
    score: w.score,
    band: filledBands[i],
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
              formatter={(value: any, name: string | undefined) => {
                if (name === "band" && Array.isArray(value)) {
                  return [`${value[0]} – ${value[1]}`, "Optimal range"];
                }
                return [value ?? 0, "Weekly load"];
              }}
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
