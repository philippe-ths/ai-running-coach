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
import {
  SufferScorePoint,
  DailySufferScorePoint,
  WeeklySufferScorePoint,
  PeriodSufferScorePoint,
  TrendsGranularity,
} from "@/lib/types";
import ChartTooltip from "@/components/charts/ChartTooltip";
import { TYPICAL_LINE_PROPS, renderTypicalLabel } from "./typicalLine";
import { BUCKET_NOUN, bucketAxisLabel, bucketKey } from "./granularity";

interface Props {
  data:
    | SufferScorePoint[]
    | DailySufferScorePoint[]
    | WeeklySufferScorePoint[]
    | PeriodSufferScorePoint[];
  granularity: TrendsGranularity;
  /** Rolling mode (#630): coarse buckets roll back from today and are labelled by
   * their end day rather than snapping to calendar chunks. */
  rolling: boolean;
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
  /** Runner's typical load per bucket, in chart units (#413). Draws a dashed
   * reference line; omitted when no norm exists. */
  typical?: number;
}

export default function SufferScoreChart({ data, granularity, rolling, delta, typical }: Props) {
  // #566: an edge bucket straddles the selected period boundary; render the
  // whole bucket as a stacked bar — in-range load solid, out-of-range load
  // faded on top — so a partial week/period isn't misread as a low one and the
  // same bucket reads at the same height across ranges. Daily/per-activity
  // points carry no out-of-period value, so they render as a single bar.
  const chartData = data.map((d: any) => {
    const inRaw = d.effort_score ?? 0;
    const outRaw = d.out_of_period_effort_score ?? 0;
    const outDays = d.out_of_period_days ?? 0;
    const inDays = d.in_period_days ?? null;
    return {
      ...d,
      inValue: inRaw,
      outValue: outRaw > 0 ? outRaw : null,
      inRaw,
      outRaw,
      label: bucketAxisLabel(bucketKey(d), granularity, rolling),
      partial: outDays > 0,
      inDays,
      totalDays: inDays != null ? inDays + outDays : null,
    };
  });

  const noun = BUCKET_NOUN[granularity];
  const title = `Accumulated Load per ${noun}`;
  const hasOutSegment = chartData.some((d: any) => d.outValue != null);
  const partialNoun = noun.toLowerCase();
  const fmtRaw = (raw: number) => Math.round(raw).toLocaleString();

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">
        {title}
      </h3>
      {delta && <div className="mb-1">{delta}</div>}
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
                  formatter={(_value, _name, entry) => {
                    const p: any = entry.payload;
                    if (entry.dataKey === "outValue") {
                      return [fmtRaw(p?.outRaw ?? 0), "Outside range"];
                    }
                    const inName = p?.partial
                      ? `In range · ${p.inDays}/${p.totalDays} days`
                      : "Accumulated Load";
                    return [fmtRaw(p?.inRaw ?? 0), inName];
                  }}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            {/* Stacked: in-range (solid) + out-of-range (faded) = whole bucket. */}
            <Bar
              dataKey="inValue"
              stackId="load"
              fill="#ef4444"
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
            <Bar
              dataKey="outValue"
              stackId="load"
              fill="#ef4444"
              fillOpacity={0.32}
              radius={[4, 4, 0, 0]}
              maxBarSize={40}
            />
            {typical != null && typical > 0 && (
              <ReferenceLine
                y={typical}
                {...TYPICAL_LINE_PROPS}
                label={renderTypicalLabel(`typical · ${Math.round(typical).toLocaleString()}`)}
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
