"use client";

import { useState } from "react";
import type { ReactNode } from "react";
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

export default function LoadTrendChart({ weeks, selectedWeekStart }: Props) {
  // The 4-week view is the one that answers what this card exists to ask —
  // is this week's load inside the range built from the 4 weeks before it —
  // so it opens there; "All" (52 weeks) is history, a tap away.
  const [view, setView] = useState<TrendView>("4w");

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

  // #948: everything below follows the SELECTED week (the week-navigation
  // arrows on the page above), not always the last entry — a runner stepping
  // back through history must see that week's own range/status, not today's.
  const selectedIndex = selectedWeekStart
    ? weeks.findIndex((w) => w.week_start === selectedWeekStart)
    : -1;
  const effectiveIndex = selectedIndex >= 0 ? selectedIndex : weeks.length - 1;

  // The 4-week view shows the 4 weeks PRECEDING the selected week plus the
  // selected week itself (#565) — i.e. the trailing window the selected week's
  // optimal range is computed from (backend training_load.py: the band is
  // 0.8-1.3x the mean of the 4 weeks before each week). Slice after the band
  // fill so the carried-forward optimal range stays correct.
  const WEEKS_IN_4W_VIEW = 5; // 4 trailing + selected
  // Both views slice `chartData` starting at `sliceStart` ("All" starts at 0,
  // i.e. no slicing) — computed once and reused below for the dot highlight,
  // so the two can never disagree about which rendered point is the selected
  // week.
  const sliceStart =
    view === "4w" ? Math.max(0, effectiveIndex + 1 - WEEKS_IN_4W_VIEW) : 0;
  const visibleData =
    view === "4w" ? chartData.slice(sliceStart, effectiveIndex + 1) : chartData;
  // `visibleData` is a SLICE of `chartData`, so the selected week's position
  // in the rendered series is not `effectiveIndex` itself but its offset from
  // where the slice starts. In "All" view sliceStart is 0, so this reduces to
  // effectiveIndex unchanged; in "4w" view it is effectiveIndex's distance
  // from the trailing edge of the 5-week window.
  const selectedVisibleIndex = effectiveIndex - sliceStart;

  // Derivation of the selected week's optimal range, for the 4-week view
  // visual: the trailing 4-week average (the chronic baseline) and the band
  // around it that the selected week is judged against.
  const currentWeek = weeks[effectiveIndex];
  const trailingWeeks = weeks.slice(Math.max(0, effectiveIndex - 4), effectiveIndex);
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

  // The selected week's own status, for the trend-line marker below — the
  // same directional read DerivationStrip renders as its "current week" dot,
  // just expressed as SVG-usable hex rather than a Tailwind bg-* class.
  const selectedStatusHex = STATUS_HEX[currentWeek?.status ?? ""] ?? STATUS_HEX.default;
  const isCurrentReal = effectiveIndex === weeks.length - 1;

  // Custom dot for the `score` Line, called once per rendered point. Every
  // week keeps today's plain marker (r=2, white fill / blue ring — recharts'
  // own default for `dot={{ r: 2 }}` on this Line); only the SELECTED week
  // (by its index within the rendered/visible series, not its index in the
  // full history) gets the larger status-coloured marker plus a naming label,
  // in BOTH views — 52 undifferentiated points in "All" is exactly where
  // knowing which one is "now" matters most.
  //
  // The x-axis's own tick labels are not used for the naming cue: recharts
  // thins ticks (`interval="preserveEnd"`) once there isn't room for all of
  // them, and the selected week's tick can be one of the ones dropped — a
  // label added there could silently stop rendering. Anchoring the label to
  // the dot instead (rendered for every point, never thinned) keeps it
  // reliable at any history length.
  function renderScoreDot(props: {
    cx?: number;
    cy?: number;
    index?: number;
  }): ReactNode {
    const { cx, cy, index } = props;
    if (cx == null || cy == null) {
      return null;
    }
    if (index !== selectedVisibleIndex) {
      return <circle cx={cx} cy={cy} r={2} fill="#fff" stroke="#3b82f6" strokeWidth={2} />;
    }
    const label = isCurrentReal ? "This week" : "Selected week";
    // The selected week is very often the RIGHTMOST point (it's the current
    // week whenever the runner hasn't stepped back) — a middle-anchored label
    // there runs off the chart's right edge and gets clipped. Anchor from
    // whichever side has room: the first point anchors left, the last point
    // anchors right, everything in between stays centred.
    const isFirstPoint = selectedVisibleIndex === 0;
    const isLastPoint = selectedVisibleIndex === visibleData.length - 1;
    const textAnchor = isLastPoint ? "end" : isFirstPoint ? "start" : "middle";
    const textX = isLastPoint ? cx + 4 : isFirstPoint ? cx - 4 : cx;
    return (
      <g>
        <text
          x={textX}
          y={cy - 12}
          textAnchor={textAnchor}
          fontSize={10}
          fontWeight={600}
          className="fill-gray-600 dark:fill-gray-300"
        >
          {label}
        </text>
        <circle
          cx={cx}
          cy={cy}
          r={5}
          fill={selectedStatusHex}
          strokeWidth={2}
          className="stroke-white dark:stroke-gray-800"
        />
      </g>
    );
  }

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
          {/* Extra top margin reserves headroom for the selected week's
              "This week"/"Selected week" label, which sits above its marker
              and would otherwise be able to clip against the chart's edge
              when that week's score is near the top of the visible range. */}
          <ComposedChart data={visibleData} margin={{ top: 18, right: 4, left: 4, bottom: 4 }}>
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
              dot={renderScoreDot}
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

// The same palette as STATUS_FILL (Tailwind's emerald-500/amber-500/sky-500/
// gray-500), as literal hex — recharts renders the trend-line marker as raw
// SVG, whose `fill` attribute cannot take a Tailwind class. Keep in sync with
// STATUS_FILL above if that mapping ever changes.
const STATUS_HEX: Record<string, string> = {
  optimal: "#10b981",
  high: "#f59e0b",
  below: "#0ea5e9",
  default: "#6b7280",
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
