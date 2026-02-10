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
import { formatDateLabel } from "@/lib/format";

interface Props {
  data: PaceTrendPoint[];
}

function formatPace(secPerKm: number): string {
  let min = Math.floor(secPerKm / 60);
  let sec = Math.round(secPerKm % 60);
  if (sec === 60) {
    min += 1;
    sec = 0;
  }
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export default function PaceTrendChart({ data }: Props) {
  // Each activity becomes its own data point, plotted sequentially.
  // A point has either `run` or `walk` set (not both), and connectNulls
  // draws lines through the gaps for each series.
  const chartData = data.map((p, idx) => ({
    idx,
    label: formatDateLabel(p.date),
    run: p.type.toLowerCase() === "run" ? p.pace_sec_per_km : undefined,
    walk: p.type.toLowerCase() === "walk" ? p.pace_sec_per_km : undefined,
  }));

  const hasRun = data.some((p) => p.type.toLowerCase() === "run");
  const hasWalk = data.some((p) => p.type.toLowerCase() === "walk");

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
                name === "Run" ? "Run" : "Walk",
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
