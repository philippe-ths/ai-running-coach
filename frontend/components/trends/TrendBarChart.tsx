"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { ReactNode } from "react";
import { formatDateLabel } from "@/lib/format";
import ChartTooltip from "@/components/charts/ChartTooltip";
import { TYPICAL_LINE_PROPS, renderTypicalLabel } from "./typicalLine";

interface TrendBarChartProps {
  data: any[];
  type: "distance" | "time";
  granularity: "daily" | "weekly";
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
  /** Runner's typical level per bucket, in chart units (#413). Draws a dashed
   * reference line. Omitted when no norm exists (thin history / ALL range). */
  typical?: number;
}

function formatMinutes(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function TrendBarChart({
  data,
  type,
  granularity,
  delta,
  typical,
}: TrendBarChartProps) {
  // Configuration based on type
  const isDistance = type === "distance";
  const title = isDistance
    ? granularity === "daily"
      ? "Distance per Day"
      : "Distance per Week"
    : granularity === "daily"
    ? "Time per Day"
    : "Time per Week";

  const barColor = isDistance ? "#3b82f6" : "#10b981";
  const unitLabel = isDistance ? " km" : " min";

  // Data transformation
  const chartData = data.map((d) => {
    const rawValue = isDistance ? d.total_distance_m : d.total_moving_time_s;
    // For distance: meters -> km
    // For time: seconds -> minutes (for bar height)
    const chartValue = isDistance
      ? +(rawValue / 1000).toFixed(1)
      : +(rawValue / 60).toFixed(0);

    return {
      ...d,
      value: chartValue,
      rawValue: rawValue, // preserve for tooltip
      label: formatDateLabel(d.week_start ?? d.date),
    };
  });

  const tooltipPrefix = granularity === "daily" ? "" : "Week of ";

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">{title}</h3>
        {delta}
      </div>
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
              unit={unitLabel}
              width={60}
            />
            <Tooltip
              content={
                <ChartTooltip
                  formatter={(_value, _name, entry) => {
                    const raw = (entry.payload?.rawValue as number) ?? 0;
                    if (isDistance) return [`${(raw / 1000).toFixed(2)} km`, "Distance"];
                    return [formatMinutes(raw), "Moving Time"];
                  }}
                  labelFormatter={(label) => `${tooltipPrefix}${label}`}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            <Bar
              dataKey="value"
              fill={barColor}
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
            {typical != null && typical > 0 && (
              <ReferenceLine
                y={typical}
                {...TYPICAL_LINE_PROPS}
                label={renderTypicalLabel(
                  `typical · ${
                    isDistance ? `${typical.toFixed(1)} km` : formatMinutes(typical * 60)
                  }`,
                )}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
