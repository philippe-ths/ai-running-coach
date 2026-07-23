"use client";

import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from "recharts";
import { EfficiencyPoint } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";
import { ReactNode, useMemo, useState } from "react";
import ActivityTypeFilter from "./ActivityTypeFilter";

interface Props {
  data: EfficiencyPoint[];
  /** Period-over-period delta shown under the title (e.g. a <StatDiff>). */
  delta?: ReactNode;
}

function calculateSMA(data: EfficiencyPoint[], window: number = 5): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    const start = Math.max(0, i - window + 1);
    const subset = data.slice(start, i + 1);
    const sum = subset.reduce((acc, curr) => acc + curr.efficiency_mps_per_bpm, 0);
    result.push(sum / subset.length);
  }
  return result;
}

// Color map for known types (module-level so the tooltip and dots share it).
function getColor(type: string): string {
  const t = type.toLowerCase();
  if (t === "run") return "#3b82f6";
  if (t === "walk") return "#f59e0b";
  if (t === "alpineski" || t === "ride") return "#8b5cf6";
  return "#64748b";
}

const M_PER_BEAT = (eff: number) => +(eff * 60).toFixed(2);

// A chart row: an EfficiencyPoint plus derived display fields. The dynamic
// [type] key (the m/beat value under the activity's own type, for its scatter
// Line) needs the index signature.
interface EffRow extends EfficiencyPoint {
  dayOrdinal: number;
  dayKey: string;
  label: string;
  value: number;
  trend: number | null;
  [key: string]: unknown;
}

// One point per calendar day for the rolling trend Line (its own dataset so the
// line stays clean even when a day has several activities).
interface TrendRow {
  dayOrdinal: number;
  trend: number | null;
  label: string;
}

// Per-activity dot that outlines confounded activities (#746) so a hilly /
// stop-heavy point is flagged at a glance, not only on hover. Colored by the
// row's own type (each dot's payload is a single activity), and rendered as one
// shared element across every type Line. Module-level + named so it is a proper
// component (recharts clones it with cx/cy/value/payload props).
function EfficiencyDot(props: {
  cx?: number;
  cy?: number;
  index?: number;
  dataKey?: string;
  value?: number | null;
  payload?: EffRow;
}) {
  const { cx, cy, value, payload, dataKey } = props;
  const v = value ?? (payload && dataKey ? (payload[dataKey] as number | null) : null);
  if (cx == null || cy == null || v == null) return null;
  const confounded = !!(payload && (payload.hilly || payload.stoppy));
  return (
    <circle
      cx={cx}
      cy={cy}
      r={3}
      fill={getColor(payload?.type ?? "")}
      stroke={confounded ? "currentColor" : "none"}
      strokeWidth={confounded ? 1.5 : 0}
      className={confounded ? "text-gray-700 dark:text-gray-200" : undefined}
    />
  );
}

// Day-grouped tooltip (#745/#746): lists every activity on the hovered day with
// its type, m/beat value, and any condition confounders (hills, stops), plus the
// trend. Confounders are surfaced, not baked into the number (#746). Passed as an
// element so recharts injects active/payload alongside the byDay prop.
function EfficiencyTooltip(props: {
  active?: boolean;
  payload?: Array<{ payload?: EffRow | TrendRow }>;
  byDay?: Map<string, EffRow[]>;
}) {
  // The active payload can include the trend series; pick an activity row (the one
  // carrying dayKey) so the tooltip always groups by the hovered day.
  const row = (props.payload ?? [])
    .map((p) => p.payload)
    .find((p): p is EffRow => !!p && "dayKey" in p);
  if (!props.active || !row) return null;
  const acts = props.byDay?.get(row.dayKey) ?? [row];
  const trend = row.trend;
  return (
    <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-2 text-xs shadow-md">
      <div className="font-semibold mb-1 text-gray-700 dark:text-gray-200">{row.label}</div>
      {acts.map((a) => (
        <div key={a.activity_id} className="flex items-center gap-1.5 whitespace-nowrap">
          <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: getColor(a.type) }} />
          <span className="capitalize text-gray-600 dark:text-gray-300">{a.type}</span>
          <span className="font-mono text-gray-800 dark:text-gray-100">{M_PER_BEAT(a.efficiency_mps_per_bpm).toFixed(2)} m/beat</span>
          {(a.hilly || a.stoppy) && (
            <span className="text-gray-400 dark:text-gray-500">
              {a.hilly ? ` · hilly ${Math.round(a.gain_per_km)} m/km` : ""}
              {a.stoppy ? ` · stops ${Math.round(a.stopped_frac * 100)}%` : ""}
            </span>
          )}
        </div>
      ))}
      {trend != null && (
        <div className="mt-1 pt-1 border-t border-gray-100 dark:border-gray-700 text-gray-500 dark:text-gray-400">
          Trend (5-act avg): {trend.toFixed(2)} m/beat
        </div>
      )}
    </div>
  );
}

export default function EfficiencyTrendChart({ data, delta }: Props) {
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);

  // Derive available types from the dataset
  const availableTypes = useMemo(() => {
    const types = new Set(data.map((p) => p.type));
    return Array.from(types).sort();
  }, [data]);

  const chartData = useMemo(() => {
    // 1. Filter data based on selection
    let filtered =
      selectedTypes.length === 0
        ? data
        : data.filter((p) => selectedTypes.includes(p.type));

    // 2. Sort chronologically to ensure trend line is correct
    filtered = [...filtered].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

    // 3. Calculate SMA on the filtered subset
    const sma = calculateSMA(filtered, 5);

    // 4. Anchor the x-axis to the DAY, not to each activity (#745). Each distinct
    //    calendar day gets an ordinal, and every activity that day shares that x —
    //    so a run + walk + ride on one day stack vertically under the same date and
    //    the trend/cursor lines up with all of them, instead of being spread across
    //    adjacent slots. Each activity is still its own dot (reachable), and the
    //    tooltip still lists every activity that day. Per-day trend = the SMA of the
    //    last activity that day, so the trend Line stays one clean point per day.
    const dayOrder: string[] = [];
    const ordinalOf = new Map<string, number>();
    const trendOf = new Map<string, number | null>();
    filtered.forEach((p, i) => {
      const d = p.date.slice(0, 10);
      if (!ordinalOf.has(d)) { ordinalOf.set(d, dayOrder.length); dayOrder.push(d); }
      trendOf.set(d, sma[i] != null ? +(sma[i]! * 60).toFixed(2) : null); // last activity of day wins
    });

    const rows = filtered.map((p) => {
      const d = p.date.slice(0, 10);
      return {
        ...p,
        dayOrdinal: ordinalOf.get(d)!,
        dayKey: d,
        label: formatDateLabel(p.date),
        value: M_PER_BEAT(p.efficiency_mps_per_bpm),
        [p.type]: M_PER_BEAT(p.efficiency_mps_per_bpm),
        trend: trendOf.get(d) ?? null,
      };
    }) as EffRow[];

    const trendData: TrendRow[] = dayOrder.map((d, i) => ({
      dayOrdinal: i,
      trend: trendOf.get(d) ?? null,
      label: formatDateLabel(d),
    }));

    return { rows, trendData, dayOrder };
  }, [data, selectedTypes]);

  // Group every activity by calendar day so the tooltip can list all activities
  // that share a day (#745), not just the one under the cursor.
  const byDay = useMemo(() => {
    const m = new Map<string, EffRow[]>();
    for (const r of chartData.rows) {
      const arr = m.get(r.dayKey);
      if (arr) arr.push(r);
      else m.set(r.dayKey, [r]);
    }
    return m;
  }, [chartData]);

  // Thin the day ordinals to ~8 axis ticks, each formatted as its date.
  const tickVals = useMemo(() => {
    const n = chartData.dayOrder.length;
    if (n <= 1) return n === 1 ? [0] : [];
    const step = Math.max(1, Math.ceil(n / 8));
    const t: number[] = [];
    for (let i = 0; i < n; i += step) t.push(i);
    if (t[t.length - 1] !== n - 1) t.push(n - 1);
    return t;
  }, [chartData.dayOrder]);

  // Extract unique types from chartData for coloring/legend
  const presentTypes = useMemo(() => {
    const types = new Set(chartData.rows.map((p) => p.type));
    return Array.from(types);
  }, [chartData]);

  // Efficiency is inherently per-activity (a scatter of individual runs with a
  // rolling-activity trend), so the bar-granularity control does not bucket it.
  const title = "Heart Rate Efficiency per Activity";

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">{title}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Meters per heartbeat. Higher is better.
          </p>
          {delta}
        </div>
        <div>
          <ActivityTypeFilter
            available={availableTypes}
            selected={selectedTypes}
            onChange={setSelectedTypes}
          />
        </div>
      </div>

      {chartData.rows.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No sufficient heart rate data for this range.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartData.rows}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="dayOrdinal"
                type="number"
                domain={[-0.5, Math.max(0, chartData.dayOrder.length - 1) + 0.5]}
                ticks={tickVals}
                allowDecimals={false}
                tickFormatter={(v) => {
                  const d = chartData.dayOrder[Number(v)];
                  return d ? formatDateLabel(d) : "";
                }}
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 12 }}
                tickLine={false}
                axisLine={false}
                width={50}
                domain={["auto", "auto"]}
                tickFormatter={(val) => `${val.toFixed(1)} m`}
                unit=""
              />
              <Tooltip content={<EfficiencyTooltip byDay={byDay} />} />
              <Legend />

              {/* Trend Line — its own one-point-per-day dataset so it stays clean
                  even when a day has several activities. */}
              <Line
                data={chartData.trendData}
                type="monotone"
                dataKey="trend"
                stroke="#64748b" // slate-500
                strokeWidth={2}
                dot={false}
                name="Trend"
                isAnimationActive={false}
              />

              {/* Dynamic Scatter Lines per Type */}
              {presentTypes.map((type) => (
               <Line
                  key={type}
                  type="monotone"
                  dataKey={type}
                  stroke={getColor(type)}
                  strokeWidth={0}
                  dot={<EfficiencyDot />}
                  activeDot={{ r: 5 }}
                  connectNulls={false}
                  name={type}
                  isAnimationActive={false}
                />
              ))}

            </ComposedChart>
        </ResponsiveContainer>
      )}

      <div className="mt-3 text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-700/50 p-2 rounded">
        Efficiency is affected by heat, hills, wind, terrain, and stops. Hilly and
        stop-heavy activities are outlined and flagged on hover; heat is not yet
        flagged. Compare similar routes/efforts for best signal.
      </div>
    </div>
  );
}
