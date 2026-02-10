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
import { WeeklyDistancePoint } from "@/lib/types";
import { format, parseISO } from "date-fns";

interface Props {
  data: WeeklyDistancePoint[];
}

export default function WeeklyDistanceChart({ data }: Props) {
  const chartData = data.map((d) => ({
    ...d,
    distance_km: +(d.total_distance_m / 1000).toFixed(1),
    label: format(parseISO(d.week_start), "MMM d"),
  }));

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">
        Distance per Week
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
              unit=" km"
              width={60}
            />
            <Tooltip
              formatter={(value: number | undefined) => [`${value ?? 0} km`, "Distance"]}
              labelFormatter={(label) => `Week of ${label}`}
            />
            <Bar
              dataKey="distance_km"
              fill="#3b82f6"
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
