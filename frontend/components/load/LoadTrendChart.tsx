"use client";

import { useState } from "react";
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

type TrendView = "all" | "4w";

export default function LoadTrendChart({ weeks }: Props) {
  const [view, setView] = useState<TrendView>("all");

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

  // Slice after the band fill so the carried-forward optimal range stays correct
  // in the zoomed-in 4-week view.
  const visibleData = view === "4w" ? chartData.slice(-4) : chartData;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          Weekly Load Trend
        </h3>
        {chartData.length > 0 && (
          <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-1 gap-0.5">
            {(
              [
                { key: "all", label: "All" },
                { key: "4w", label: "4 weeks" },
              ] as { key: TrendView; label: string }[]
            ).map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                aria-pressed={view === key}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  view === key
                    ? "bg-blue-600 text-white shadow-sm"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-4">
        Weekly load against your optimal range (0.8–1.3× your trailing 4-week average).
      </p>
      {chartData.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No load history yet.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={visibleData}>
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
