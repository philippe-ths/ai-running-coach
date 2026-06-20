"use client";

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel, formatDistanceKm, formatDuration } from "@/lib/format";
import type { TrendsRange } from "@/lib/types/trends";
import type {
  VolumeDirection,
  VolumeFraming,
  VolumeMetricVsNorm,
  VolumeReport,
} from "@/lib/types/volume";

type FramingKey = "rolling" | "calendar";

const METRIC_LABELS: Record<string, string> = {
  sessions: "Sessions",
  distance_m: "Distance",
  moving_time_s: "Moving time",
  effort_score: "Training load",
};

function formatMetric(metric: string, value: number): string {
  if (metric === "distance_m") return formatDistanceKm(value);
  if (metric === "moving_time_s") return formatDuration(value);
  return Math.round(value).toString(); // sessions, effort_score
}

// A down window is not alarming — it is usually deliberate — so "down" reads calm
// (sky), an up window reads as something to weigh (amber), in-line is neutral.
function directionStyle(d: VolumeDirection): { label: string; cls: string; icon: JSX.Element } {
  switch (d) {
    case "down":
      return {
        label: "down",
        cls: "text-sky-700 bg-sky-50 dark:text-sky-300 dark:bg-sky-900/30",
        icon: <ArrowDown className="w-3.5 h-3.5" />,
      };
    case "up":
      return {
        label: "up",
        cls: "text-amber-700 bg-amber-50 dark:text-amber-300 dark:bg-amber-900/30",
        icon: <ArrowUp className="w-3.5 h-3.5" />,
      };
    case "in_line":
      return {
        label: "in line",
        cls: "text-gray-600 bg-gray-100 dark:text-gray-300 dark:bg-gray-700/50",
        icon: <Minus className="w-3.5 h-3.5" />,
      };
    default:
      return {
        label: "no norm yet",
        cls: "text-gray-400 bg-gray-50 dark:text-gray-500 dark:bg-gray-800",
        icon: <Minus className="w-3.5 h-3.5" />,
      };
  }
}

function MetricRow({ m }: { m: VolumeMetricVsNorm }) {
  const style = directionStyle(m.direction);
  const runsSuffix =
    m.metric === "sessions"
      ? ` (${Math.round(m.current_runs)} ${m.current_runs === 1 ? "run" : "runs"})`
      : m.metric === "distance_m"
        ? ` (${formatDistanceKm(m.current_runs)} running)`
        : "";

  return (
    <div className="flex items-center justify-between py-2.5 border-b border-gray-100 dark:border-gray-700/60 last:border-0">
      <div className="min-w-0">
        <div className="text-sm text-gray-500 dark:text-gray-400">
          {METRIC_LABELS[m.metric] ?? m.metric}
        </div>
        <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {formatMetric(m.metric, m.current_all)}
          <span className="text-xs font-normal text-gray-400 dark:text-gray-500">{runsSuffix}</span>
        </div>
      </div>
      <div className="text-right">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${style.cls}`}
        >
          {style.icon}
          {style.label}
          {m.pct_vs_norm !== null && m.direction !== "no_norm" ? (
            <span className="opacity-70">
              {m.pct_vs_norm > 0 ? "+" : ""}
              {Math.round(m.pct_vs_norm)}%
            </span>
          ) : null}
        </span>
        {m.norm !== null && (
          <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
            norm {formatMetric(m.metric, m.norm)}
          </div>
        )}
      </div>
    </div>
  );
}

export default function VsNormCard({
  range,
  mode,
}: {
  range: TrendsRange;
  mode: FramingKey;
}) {
  const [data, setData] = useState<VolumeReport | null>(null);

  useEffect(() => {
    if (range === "ALL") return;
    let active = true;
    fetchFromAPI(`/api/trends/volume?range=${range}`)
      .then((d: VolumeReport) => {
        if (active) setData(d);
      })
      .catch(() => active && setData(null));
    return () => {
      active = false;
    };
  }, [range]);

  // "All" has no bounded current period to compare to a norm.
  if (range === "ALL" || !data) return null;

  // The framing follows the page-level Rolling/Calendar toggle.
  const win: VolumeFraming = data[mode];
  const dateRange = `${formatDateLabel(win.period_start)} – ${formatDateLabel(win.period_end)}`;
  const subtitle =
    win.framing === "rolling" || win.complete
      ? `${win.label} (${dateRange}) vs your typical`
      : `${win.label} so far (${dateRange}, ${win.days_elapsed}/${win.window_days} days) vs your typical`;
  const baselineRange =
    win.baseline_start && win.baseline_end
      ? `${formatDateLabel(win.baseline_start)} – ${formatDateLabel(win.baseline_end)}`
      : null;

  return (
    <div className="bg-white dark:bg-gray-800 p-5 rounded-lg border dark:border-gray-700 shadow-sm">
      <div className="mb-2">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          How your training compares to your norm
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">{subtitle}</p>
      </div>

      {data.has_baseline ? (
        <div>
          {win.metrics.map((m) => (
            <MetricRow key={m.metric} m={m} />
          ))}
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-3">
            Norm is your typical output for a window this size, from {data.baseline_label}
            {baselineRange ? ` (${baselineRange})` : ""}. A down window is often deliberate.
          </p>
        </div>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-4">
          Still learning your norm — once you have a few more weeks of history, this will show how
          your training compares to your typical window.
        </p>
      )}
    </div>
  );
}
