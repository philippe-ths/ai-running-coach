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
import { SufferScorePoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";

interface Props {
  data: SufferScorePoint[];
}

export default function SufferScoreChart({ data }: Props) {
  const chartData = data.map((d) => ({
    ...d,
    label: formatDateLabel(d.date),
  }));

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">
        Suffer Score Over Time
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
