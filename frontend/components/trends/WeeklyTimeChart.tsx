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
import { WeeklyTimePoint, DailyTimePoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";

interface Props {
  data: WeeklyTimePoint[] | DailyTimePoint[];
  granularity: "daily" | "weekly";
}

function formatMinutes(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function WeeklyTimeChart({ data, granularity }: Props) {
  const chartData = data.map((d: any) => ({
    ...d,
    time_min: +(d.total_moving_time_s / 60).toFixed(0),
    label: formatDateLabel(d.week_start ?? d.date),
  }));

  const title = granularity === "daily" ? "Time per Day" : "Time per Week";
  const tooltipPrefix = granularity === "daily" ? "" : "Week of ";

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
              unit=" min"
              width={65}
            />
            <Tooltip
              formatter={(value: number | undefined) => [
                formatMinutes((value ?? 0) * 60),
                "Moving Time",
              ]}
              labelFormatter={(label) => `${tooltipPrefix}${label}`}
            />
            <Bar
              dataKey="time_min"
              fill="#10b981"
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
