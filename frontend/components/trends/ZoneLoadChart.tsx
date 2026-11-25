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
import { ReactNode } from "react";
import {
  ZoneLoadWeekPoint,
  DailyZoneLoadPoint,
  PeriodZoneLoadPoint,
  TrendsGranularity,
} from "@/lib/types";
import { BUCKET_NOUN, bucketAxisLabel, bucketKey } from "./granularity";
import { formatDuration } from "@/lib/format";

/** Minutes as "Xh Ym" / "X min", matching the other time metrics on the page. */
function formatMinutes(minutes: number): string {
  return formatDuration(Math.round(minutes) * 60);
}

/** Compact axis label: "45m", "1h", "1h 30m". */
function formatAxisMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h === 0) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** Y-axis ticks on clean time steps (15m, 30m, 1h, 2h...) so labels never land
 * on awkward values like 1h 40m. Aims for at most 5 intervals. */
const TICK_STEPS_MIN = [15, 30, 60, 120, 180, 240, 300, 360, 600, 1200];
function timeTicks(maxMinutes: number): number[] {
  const step =
    TICK_STEPS_MIN.find((s) => maxMinutes / s <= 5) ??
    Math.ceil(maxMinutes / 5 / 600) * 600;
  const top = Math.max(step, Math.ceil(maxMinutes / step) * step);
  const ticks: number[] = [];
  for (let t = 0; t <= top; t += step) ticks.push(t);
  return ticks;
}

interface ZoneLoadChartProps {
  data: ZoneLoadWeekPoint[] | DailyZoneLoadPoint[] | PeriodZoneLoadPoint[];
  granularity: TrendsGranularity;
  /** Rolling mode (#630): coarse buckets roll back from today and are labelled by
   * their end day rather than snapping to calendar chunks. */
  rolling: boolean;
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
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
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-3 text-sm">
      <p className="font-semibold text-gray-700 dark:text-gray-300 mb-1">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex justify-between gap-6">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="font-mono">{formatMinutes(p.value)}</span>
        </div>
      ))}
      <div className="border-t dark:border-gray-700 mt-1 pt-1 flex justify-between gap-6 font-semibold text-gray-800 dark:text-gray-200">
        <span>Total</span>
        <span className="font-mono">{formatMinutes(total)}</span>
      </div>
    </div>
  );
}

export default function ZoneLoadChart({ data, granularity, rolling, delta }: ZoneLoadChartProps) {
  const title = `Training Load by Zone per ${BUCKET_NOUN[granularity]}`;

  // Check if all entries have zero zone data
  const hasAnyData = data.some(
    (d) => d.easy_min > 0 || d.moderate_min > 0 || d.hard_min > 0
  );

  if (!data.length || !hasAnyData) {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No HR zone data available yet. Sync activities with heart rate data.
        </p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    label: bucketAxisLabel(bucketKey(d), granularity, rolling),
  }));
  const yTicks = timeTicks(
    Math.max(...data.map((d) => d.easy_min + d.moderate_min + d.hard_min))
  );

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">{title}</h3>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
          Easy (&lt;70% HR) · Moderate (70-80%) · Hard (&gt;80%)
        </p>
        {delta}
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
            ticks={yTicks}
            domain={[0, yTicks[yTicks.length - 1]]}
            tickFormatter={formatAxisMinutes}
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
