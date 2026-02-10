"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { PaceTrendPoint } from "@/lib/types";
import { format, parseISO } from "date-fns";

interface Props {
  data: PaceTrendPoint[];
}

function formatPace(secPerKm: number): string {
  const min = Math.floor(secPerKm / 60);
  const sec = Math.round(secPerKm % 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export default function PaceTrendChart({ data }: Props) {
  // Separate run vs walk, then merge by date for multi-line chart.
  // For simplicity, plot all points on a single timeline per type.
  const runPoints = data
    .filter((p) => p.type.toLowerCase() === "run")
    .map((p) => ({
      date: p.date,
      label: format(parseISO(p.date), "MMM d"),
      run: p.pace_sec_per_km,
    }));

  const walkPoints = data
    .filter((p) => p.type.toLowerCase() === "walk")
    .map((p) => ({
      date: p.date,
      label: format(parseISO(p.date), "MMM d"),
      walk: p.pace_sec_per_km,
    }));

  // Merge into a single dataset keyed by date
  const merged = new Map<string, { date: string; label: string; run?: number; walk?: number }>();

  for (const p of runPoints) {
    merged.set(p.date, { ...merged.get(p.date), ...p });
  }
  for (const p of walkPoints) {
    merged.set(p.date, { ...merged.get(p.date), ...p });
  }

  const chartData = Array.from(merged.values()).sort(
    (a, b) => a.date.localeCompare(b.date)
  );

  const hasRun = runPoints.length > 0;
  const hasWalk = walkPoints.length > 0;

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">
        Avg Pace Trend
      </h3>
      {chartData.length === 0 ? (
        <p className="text-gray-400 text-sm py-8 text-center">
          No run/walk data for this range.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData}>
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
              reversed
              tickFormatter={(v) => formatPace(v)}
              width={55}
              domain={["dataMin - 30", "dataMax + 30"]}
            />
            <Tooltip
              formatter={(value: number | undefined, name: string | undefined) => [
                `${formatPace(value ?? 0)} /km`,
                name === "run" ? "Run" : "Walk",
              ]}
              labelFormatter={(label) => label}
            />
            <Legend />
            {hasRun && (
              <Line
                type="monotone"
                dataKey="run"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
                name="Run"
              />
            )}
            {hasWalk && (
              <Line
                type="monotone"
                dataKey="walk"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
                name="Walk"
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
