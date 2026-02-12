"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { ZoneLoadWeekPoint, DailyZoneLoadPoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";

interface ZoneLoadChartProps {
  data: ZoneLoadWeekPoint[] | DailyZoneLoadPoint[];
  granularity: "daily" | "weekly";
}

const ZONE_COLORS = {
  easy: "#34d399", // emerald-400
  moderate: "#fbbf24", // amber-400
  hard: "#f87171", // red-400
};

interface PayloadItem {
  name: string;
  value: number;
  color: string;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: PayloadItem[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;

  const total = payload.reduce((s, p) => s + p.value, 0);
  if (total === 0) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-semibold text-gray-700 mb-1">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex justify-between gap-6">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="font-mono">{Math.round(p.value)} min</span>
        </div>
      ))}
      <div className="border-t mt-1 pt-1 flex justify-between gap-6 font-semibold text-gray-800">
        <span>Total</span>
        <span className="font-mono">{Math.round(total)} min</span>
      </div>
    </div>
  );
}

export default function ZoneLoadChart({ data, granularity }: ZoneLoadChartProps) {
  const title =
    granularity === "daily"
      ? "Training Load by Zone per Day"
      : "Training Load by Zone per Week";

  // Check if all entries have zero zone data
  const hasAnyData = data.some(
    (d) => d.easy_min > 0 || d.moderate_min > 0 || d.hard_min > 0
  );

  if (!data.length || !hasAnyData) {
    return (
      <div className="bg-white rounded-lg border shadow-sm p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">{title}</h3>
        <p className="text-gray-400 text-sm py-8 text-center">
          No HR zone data available yet. Sync activities with heart rate data.
        </p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    label: formatDateLabel(
      "week_start" in d ? d.week_start : d.date
    ),
  }));

  const tooltipPrefix = granularity === "daily" ? "" : "Week of ";

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
        <p className="text-xs text-gray-400 mt-0.5">
          Easy (&lt;70% HR) · Moderate (70-80%) · Hard (&gt;80%)
        </p>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} barCategoryGap="20%">
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
            width={60}
          />
          <Tooltip
            content={<CustomTooltip />}
            cursor={{ fill: "rgba(0,0,0,0.05)" }}
          />
          <Legend wrapperStyle={{ fontSize: 13, paddingTop: 4 }} />
          <Bar
            dataKey="easy_min"
            name="Easy"
            stackId="zone"
            fill={ZONE_COLORS.easy}
            radius={[0, 0, 0, 0]}
            maxBarSize={40}
          />
          <Bar
            dataKey="moderate_min"
            name="Moderate"
            stackId="zone"
            fill={ZONE_COLORS.moderate}
            radius={[0, 0, 0, 0]}
            maxBarSize={40}
          />
          <Bar
            dataKey="hard_min"
            name="Hard"
            stackId="zone"
            fill={ZONE_COLORS.hard}
            radius={[4, 4, 0, 0]}
            maxBarSize={40}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
