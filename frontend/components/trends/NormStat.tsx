import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import type { VolumeDirection, VolumeMetricVsNorm } from "@/lib/types/volume";

// A down window is not alarming — usually deliberate — so "down" reads calm (sky),
// an up window reads as something to weigh (amber), in-line is neutral.
function style(d: VolumeDirection): { label: string; cls: string; icon: JSX.Element } {
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
    default:
      return {
        label: "in line",
        cls: "text-gray-600 bg-gray-100 dark:text-gray-300 dark:bg-gray-700/50",
        icon: <Minus className="w-3.5 h-3.5" />,
      };
  }
}

/**
 * #400: a quick-view card's comparison line vs the runner's norm (replacing the
 * vs-previous-period delta). Renders nothing when there is no norm yet (thin
 * history, or the All range), so the card just shows its value.
 */
export default function NormStat({
  metric,
  format,
}: {
  metric?: VolumeMetricVsNorm;
  format: (n: number) => string;
}) {
  if (!metric || metric.direction === "no_norm" || metric.norm === null) return null;
  const s = style(metric.direction);
  return (
    <div className="flex items-center flex-wrap gap-x-2 gap-y-0.5 mt-1.5 text-sm">
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-xs font-medium ${s.cls}`}
      >
        {s.icon}
        {s.label}
        {metric.pct_vs_norm !== null ? (
          <span className="opacity-70">
            {metric.pct_vs_norm > 0 ? "+" : ""}
            {Math.round(metric.pct_vs_norm)}%
          </span>
        ) : null}
      </span>
      <span className="text-xs text-gray-400 dark:text-gray-500">norm {format(metric.norm)}</span>
    </div>
  );
}
