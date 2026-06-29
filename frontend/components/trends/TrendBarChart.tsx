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

  // meters -> km / seconds -> minutes for the bar height.
  const toChart = (raw: number) =>
    isDistance ? +(raw / 1000).toFixed(1) : +(raw / 60).toFixed(0);

  // Data transformation. #566: an edge bucket straddles the selected period
  // boundary, so part of its week/fortnight/month falls outside the window.
  // Render the WHOLE bucket as a stacked bar — the in-range value solid, the
  // out-of-range value (the bucket's days before the window) as a faded segment
  // on top — so the bar shows the full week's total and how much is in range,
  // and the same week reads at the same height across ranges.
  const chartData = data.map((d) => {
    const inRaw = isDistance ? d.total_distance_m : d.total_moving_time_s;
    const outRaw =
      (isDistance ? d.out_of_period_distance_m : d.out_of_period_moving_time_s) ?? 0;
    const outDays = d.out_of_period_days ?? 0;
    const inDays = d.in_period_days ?? null;

    return {
      ...d,
      inValue: toChart(inRaw),
      // null (not 0) so a full bucket renders no faded segment and drops the
      // "outside range" tooltip row.
      outValue: outRaw > 0 ? toChart(outRaw) : null,
      inRaw,
      outRaw,
      label: formatDateLabel(bucketKey(d)),
      partial: outDays > 0,
      inDays,
      totalDays: inDays != null ? inDays + outDays : null,
    };
  });

  const tooltipPrefix = TOOLTIP_PREFIX[granularity];
  // A faded segment is only drawn when an edge bucket has real out-of-range
  // value (a leading week reaching before the window); the trailing in-progress
  // bucket has none yet.
  const hasOutSegment = chartData.some((d) => d.outValue != null);
  const partialNoun = noun.toLowerCase();
  const fmtRaw = (raw: number) =>
    isDistance ? `${(raw / 1000).toFixed(2)} km` : formatMinutes(raw);

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
                    if (entry.dataKey === "outValue") {
                      return [fmtRaw((p?.outRaw as number) ?? 0), "Outside range"];
                    }
                    const inName = p?.partial
                      ? `In range · ${p.inDays}/${p.totalDays} days`
                      : isDistance
                        ? "Distance"
                        : "Moving Time";
                    return [fmtRaw((p?.inRaw as number) ?? 0), inName];
                  }}
                  labelFormatter={(label) => `${tooltipPrefix}${label}`}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            {/* Stacked: in-range (solid) + out-of-range (faded) so the bar is
                the whole bucket. Full buckets have a null out segment and render
                exactly as before. */}
            <Bar
              dataKey="inValue"
              stackId="bucket"
              fill={barColor}
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
            <Bar
              dataKey="outValue"
              stackId="bucket"
              fill={barColor}
              fillOpacity={0.32}
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
      {hasOutSegment && (
        <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
          The faded segment is the part of an edge {partialNoun} that falls
          outside the selected period — the bar shows the whole {partialNoun}.
        </p>
      )}
    </div>
  );
}
