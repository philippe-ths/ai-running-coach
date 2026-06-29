"use client";

import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { ReactNode } from "react";
import { TrendsGranularity } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";
import ChartTooltip from "@/components/charts/ChartTooltip";
import { TYPICAL_LINE_PROPS, renderTypicalLabel } from "./typicalLine";
import { BUCKET_NOUN, TOOLTIP_PREFIX, bucketKey } from "./granularity";

interface TrendBarChartProps {
  data: any[];
  type: "distance" | "time";
  granularity: TrendsGranularity;
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
  const noun = BUCKET_NOUN[granularity];
  const title = isDistance ? `Distance per ${noun}` : `Time per ${noun}`;

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

    // #566: an edge bucket straddles the selected period boundary, so part of
    // its week/fortnight/month falls outside the window. Fade it and annotate
    // coverage so a partial bucket doesn't read as a genuine low bucket.
    const outDays = d.out_of_period_days ?? 0;
    const inDays = d.in_period_days ?? null;
    const partial = outDays > 0;

    return {
      ...d,
      value: chartValue,
      rawValue: rawValue, // preserve for tooltip
      label: formatDateLabel(bucketKey(d)),
      partial,
      inDays,
      totalDays: inDays != null ? inDays + outDays : null,
    };
  });

  const tooltipPrefix = TOOLTIP_PREFIX[granularity];
  const hasPartial = chartData.some((d) => d.partial);
  const partialNoun = noun.toLowerCase();

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
                    const p = entry.payload;
                    const raw = (p?.rawValue as number) ?? 0;
                    const coverage =
                      p?.partial && p?.totalDays
                        ? ` · partial (${p.inDays}/${p.totalDays} days in range)`
                        : "";
                    if (isDistance)
                      return [`${(raw / 1000).toFixed(2)} km`, `Distance${coverage}`];
                    return [formatMinutes(raw), `Moving Time${coverage}`];
                  }}
                  labelFormatter={(label) => `${tooltipPrefix}${label}`}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={40}>
              {chartData.map((d, i) => (
                <Cell
                  key={i}
                  fill={barColor}
                  fillOpacity={d.partial ? 0.4 : 1}
                />
              ))}
            </Bar>
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
      {hasPartial && (
        <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
          Faded bars are partial — only part of the {partialNoun} falls inside the
          selected period.
        </p>
      )}
    </div>
  );
}
