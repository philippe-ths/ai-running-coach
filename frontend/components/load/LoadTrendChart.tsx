"use client";

import { useState } from "react";
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { LoadWeek } from "@/lib/types";
import { formatDateLabel } from "@/lib/format";
import ChartTooltip from "@/components/charts/ChartTooltip";

interface Props {
  weeks: LoadWeek[];
  selectedWeekStart?: string;
}

type TrendView = "all" | "4w";

export default function LoadTrendChart({ weeks }: Props) {
  const [view, setView] = useState<TrendView>("all");

  // Per-week optimal range; null for early weeks that lack a trailing baseline.
  const rawBands = weeks.map((w) =>
    w.target_min != null && w.target_max != null
      ? ([w.target_min, w.target_max] as [number, number])
      : null,
  );
  // Carry the nearest known range into bandless weeks so the optimal band spans
  // the whole chart instead of only the recent weeks that have a baseline.
  const firstKnown = rawBands.find((b) => b != null) ?? null;
  let lastKnown = firstKnown;
  const filledBands = rawBands.map((b) => {
    if (b != null) lastKnown = b;
    return b ?? lastKnown;
  });

  const chartData = weeks.map((w, i) => ({
    label: formatDateLabel(w.week_start),
    score: w.score,
    band: filledBands[i],
  }));

  // The 4-week view shows the 4 weeks PRECEDING the current week plus the
  // current week itself (#565) — i.e. the trailing window the current week's
  // optimal range is computed from (backend training_load.py: the band is
  // 0.8-1.3x the mean of the 4 weeks before each week). Slice after the band
  // fill so the carried-forward optimal range stays correct.
  const WEEKS_IN_4W_VIEW = 5; // 4 trailing + current
  const visibleData = view === "4w" ? chartData.slice(-WEEKS_IN_4W_VIEW) : chartData;

  // Derivation of the current week's optimal range, for the 4-week view visual:
  // the trailing 4-week average (the chronic baseline) and the band around it
  // that the current week is judged against. The current week is the last entry.
  const currentWeek = weeks[weeks.length - 1];
  const trailingWeeks = weeks.slice(Math.max(0, weeks.length - 1 - 4), weeks.length - 1);
  const trailingAvg =
    trailingWeeks.length > 0
      ? trailingWeeks.reduce((sum, w) => sum + w.score, 0) / trailingWeeks.length
      : null;
  const derivation =
    currentWeek != null &&
    currentWeek.target_min != null &&
    currentWeek.target_max != null &&
    trailingAvg != null
      ? {
          avg: trailingAvg,
          min: currentWeek.target_min,
          max: currentWeek.target_max,
          score: currentWeek.score,
          status: currentWeek.status,
        }
      : null;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 shadow-sm p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          Weekly Load Trend
        </h3>
        {chartData.length > 0 && (
          <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-1 gap-0.5">
            {(
              [
                { key: "all", label: "All" },
                { key: "4w", label: "4 weeks" },
              ] as { key: TrendView; label: string }[]
            ).map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => setView(key)}
                aria-pressed={view === key}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  view === key
                    ? "bg-blue-600 text-white shadow-sm"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-4">
        Weekly load against your optimal range (0.8–1.3× your trailing 4-week average).
      </p>
      {chartData.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-500 text-sm py-8 text-center">
          No load history yet.
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={visibleData}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 12 }} tickLine={false} axisLine={false} />
            <YAxis tick={{ fontSize: 12 }} tickLine={false} axisLine={false} width={40} />
            <Tooltip
              content={
                <ChartTooltip
                  formatter={(value, name) => {
                    if (name === "band" && Array.isArray(value)) {
                      return [`${value[0]} – ${value[1]}`, "Optimal range"];
                    }
                    return [(value as number) ?? 0, "Weekly load"];
                  }}
                />
              }
            />
            <Area
              dataKey="band"
              stroke="none"
              fill="#22c55e"
              fillOpacity={0.15}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              dataKey="score"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={{ r: 2 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* #565: in the 4-week view, show how the current week's optimal range is
          built — the trailing 4-week average and the 0.8-1.3x band around it,
          with the current week marked against it. */}
      {view === "4w" && derivation && <DerivationStrip {...derivation} />}
    </div>
  );
}

// The calm directional palette for where the current week landed: emerald in
// range, amber over, sky under (never red, since a quiet week is not "bad").
const STATUS_FILL: Record<string, string> = {
  optimal: "bg-emerald-500",
  high: "bg-amber-500",
  below: "bg-sky-500",
};

function DerivationStrip({
  avg,
  min,
  max,
  score,
  status,
}: {
  avg: number;
  min: number;
  max: number;
  score: number;
  status: string;
}) {
  // Track spans 0..scaleMax with headroom so the marker never sits on the edge.
  const scaleMax = Math.max(score, max, avg) * 1.15 || 1;
  const pct = (v: number) => Math.max(0, Math.min(100, (v / scaleMax) * 100));
  const fill = STATUS_FILL[status] ?? "bg-gray-500";
  const statusLabel =
    status === "optimal"
      ? "in range"
      : status === "high"
        ? "above range"
        : status === "below"
          ? "below range"
          : status;

  return (
    <div className="mt-4 rounded-md border border-gray-100 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-900/30 p-3">
      <p className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-3">
        How this week&apos;s range is set
      </p>
      <div
        className="relative h-2.5 rounded-full bg-gray-200 dark:bg-gray-700"
        role="img"
        aria-label={`This week ${Math.round(score)}, ${statusLabel}; optimal range ${min} to ${max}, trailing 4-week average ${Math.round(avg)}`}
      >
        {/* the 0.8-1.3x optimal band */}
        <div
          className="absolute inset-y-0 rounded-full bg-emerald-400/40 dark:bg-emerald-500/30"
          style={{ left: `${pct(min)}%`, right: `${100 - pct(max)}%` }}
        />
        {/* trailing 4-week average (the band's basis) */}
        <div
          className="absolute -top-1.5 -bottom-1.5 w-0.5 -translate-x-1/2 rounded-full bg-gray-500 dark:bg-gray-300"
          style={{ left: `${pct(avg)}%` }}
        />
        {/* current week */}
        <div
          className={`absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white dark:ring-gray-800 ${fill}`}
          style={{ left: `${pct(score)}%` }}
        />
      </div>
      <div className="mt-3 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        <span>
          Trailing 4-wk avg{" "}
          <span className="font-semibold text-gray-700 dark:text-gray-200">{Math.round(avg)}</span>
        </span>
        <span>
          Optimal range{" "}
          <span className="font-semibold text-emerald-600 dark:text-emerald-400">
            {min}–{max}
          </span>
          <span className="text-gray-400 dark:text-gray-500"> (0.8–1.3×)</span>
        </span>
        <span>
          This week{" "}
          <span className="font-semibold text-gray-700 dark:text-gray-200">{Math.round(score)}</span>{" "}
          <span className="text-gray-400 dark:text-gray-500">({statusLabel})</span>
        </span>
      </div>
    </div>
  );
}
