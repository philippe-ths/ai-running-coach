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
import ChartTooltip from "@/components/charts/ChartTooltip";

interface Props {
  data: SufferScorePoint[] | DailySufferScorePoint[] | WeeklySufferScorePoint[];
  granularity: "daily" | "weekly";
}

export default function SufferScoreChart({ data, granularity }: Props) {
  const chartData = data.map((d: any) => ({
    ...d,
    label: formatDateLabel(d.date ?? d.week_start),
  }));

  const title = `Accumulated Load per ${granularity === "daily" ? "Day" : "Week"}`;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">
        {title}
      </h3>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-4">
        Accumulates with both duration and intensity. Not a measure of how hard you ran.
      </p>
      {chartData.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
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
              content={
                <ChartTooltip
                  formatter={(value) => [(value as number) ?? 0, "Accumulated Load"]}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
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
