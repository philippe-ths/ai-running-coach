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
import { formatDateLabel } from "@/lib/format";

interface TrendBarChartProps {
  data: any[];
  type: "distance" | "time";
  granularity: "daily" | "weekly";
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

  const formatTooltipValue = (val: number, entry: any) => {
    // entry.payload contains the full data object
    const raw = entry.payload.rawValue;
    if (isDistance) {
      return [`${(raw / 1000).toFixed(2)} km`, "Distance"];
    } else {
      return [formatMinutes(raw), "Moving Time"];
    }
  };

  return (
    <div className="bg-white rounded-lg border shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">{title}</h3>
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
              unit={unitLabel}
              width={60}
            />
            {/* 
              Recharts Tooltip formatter signature can vary.
              We want to format the 'value' but specifically look at rawValue.
              However, the simplest approach for Recharts is to just format the value we plotted
              OR pass a custom content.
              Actually, the formatter callback receives (value, name, props).
              Props.payload is the data item.
            */}
            <Tooltip
              formatter={(value, name, props) => {
                if (props && props.payload) {
                   // When hovering, props.payload is the data object
                   const raw = props.payload.rawValue;
                   if (isDistance) return [`${(raw / 1000).toFixed(2)} km`, "Distance"];
                   return [formatMinutes(raw), "Moving Time"];
                }
                return [value, name];
              }}
              labelFormatter={(label) => `${tooltipPrefix}${label}`}
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            <Bar
              dataKey="value"
              fill={barColor}
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
