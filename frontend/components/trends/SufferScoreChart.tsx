"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { SufferScorePoint, DailySufferScorePoint, WeeklySufferScorePoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";

interface Props {
  data: SufferScorePoint[] | DailySufferScorePoint[] | WeeklySufferScorePoint[];
  granularity: "daily" | "weekly";
}

export default function SufferScoreChart({ data, granularity }: Props) {
  const chartData = data.map((d: any) => ({
    ...d,
    label: formatDateLabel(d.date ?? d.week_start),
  }));

  const title = `Suffer Score per ${granularity === "daily" ? "Day" : "Week"}`;

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">
        {title}
      </h3>
      {chartData.length === 0 ? (
        <p className="text-gray-400 text-sm py-8 text-center">
          No data for this range.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={chartData}>
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
            />
            <Tooltip
              formatter={(value: number | undefined) => [value ?? 0, "Suffer Score"]}
              labelFormatter={(label) => label}
            />
            <Bar
              dataKey="effort_score"
              fill="#ef4444"
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
