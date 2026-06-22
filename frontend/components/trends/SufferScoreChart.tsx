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
import { formatDateLabel } from "@/lib/format";
import ChartTooltip from "@/components/charts/ChartTooltip";
import { TYPICAL_LINE_PROPS, renderTypicalLabel } from "./typicalLine";
import { BUCKET_NOUN, bucketKey } from "./granularity";

interface Props {
  data:
    | SufferScorePoint[]
    | DailySufferScorePoint[]
    | WeeklySufferScorePoint[]
    | PeriodSufferScorePoint[];
  granularity: TrendsGranularity;
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
  /** Runner's typical load per bucket, in chart units (#413). Draws a dashed
   * reference line; omitted when no norm exists. */
  typical?: number;
}

export default function SufferScoreChart({ data, granularity, delta, typical }: Props) {
  const chartData = data.map((d: any) => ({
    ...d,
    label: formatDateLabel(bucketKey(d)),
  }));

  const title = `Accumulated Load per ${BUCKET_NOUN[granularity]}`;

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
                  formatter={(value) => [(value as number) ?? 0, "Accumulated Load"]}
                />
              }
              cursor={{ fill: "rgba(0,0,0,0.05)" }}
            />
            <Bar
              dataKey="effort_score"
              fill="#ef4444"
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
    </div>
  );
}
