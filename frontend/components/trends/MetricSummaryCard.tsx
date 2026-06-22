import ComparisonRows from "./ComparisonRows";
import NormGauge from "./NormGauge";
import type { VolumeMetricVsNorm } from "@/lib/types/volume";

/**
 * #400: a trends quick-view card. The value sits above a deviation-from-typical
 * gauge (the vs-norm read), then the two comparisons (vs typical, vs prev) in one
 * consistent, monochrome shape. The gauge is hidden when there's no norm yet (thin
 * history or the All range).
 */
export default function MetricSummaryCard({
  label,
  value,
  metric,
  current,
  previous,
  format,
  prevLabel,
}: {
  label: string;
  value: string;
  metric?: VolumeMetricVsNorm;
  current: number;
  previous?: number | null;
  format: (n: number) => string;
  prevLabel?: string;
}) {
  const hasNorm = !!metric && metric.direction !== "no_norm" && metric.norm !== null;

  return (
    <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
        {label}
      </div>
      <div className="mt-1 whitespace-nowrap font-mono text-2xl font-semibold tracking-tight tabular-nums text-gray-900 dark:text-gray-50">
        {value}
      </div>
      {hasNorm && metric && (
        <NormGauge pct={metric.pct_vs_norm ?? 0} />
      )}
      <div className="mt-1.5">
        <ComparisonRows
          metric={metric}
          current={current}
          previous={previous}
          format={format}
          prevLabel={prevLabel}
        />
      </div>
    </div>
  );
}
