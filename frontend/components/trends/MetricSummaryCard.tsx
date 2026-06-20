import StatDiff from "@/components/StatDiff";
import NormGauge from "./NormGauge";
import type { VolumeDirection, VolumeMetricVsNorm } from "@/lib/types/volume";

const STATUS_WORD: Record<VolumeDirection, string> = {
  down: "below",
  in_line: "in line",
  up: "above",
  no_norm: "",
};

const STATUS_TEXT: Record<VolumeDirection, string> = {
  down: "text-sky-600 dark:text-sky-300",
  up: "text-amber-600 dark:text-amber-300",
  in_line: "text-slate-500 dark:text-slate-300",
  no_norm: "",
};

/**
 * #400: a trends quick-view card. The value sits above a deviation-from-typical
 * gauge (the vs-your-norm read), with the straight vs-previous-period delta kept
 * quiet below. The gauge/norm read is hidden when there's no norm yet (thin
 * history or the All range), leaving just the value and the vs-prev delta.
 */
export default function MetricSummaryCard({
  label,
  value,
  metric,
  current,
  previous,
  format,
}: {
  label: string;
  value: string;
  metric?: VolumeMetricVsNorm;
  current: number;
  previous?: number | null;
  format: (n: number) => string;
}) {
  const hasNorm = !!metric && metric.direction !== "no_norm" && metric.norm !== null;

  return (
    <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
        {label}
      </div>
      <div className="mt-1 font-mono text-2xl font-semibold tracking-tight tabular-nums text-gray-900 dark:text-gray-50">
        {value}
      </div>

      {hasNorm && metric && (
        <>
          <NormGauge pct={metric.pct_vs_norm ?? 0} direction={metric.direction} />
          <div className="text-xs">
            <span className={`font-medium ${STATUS_TEXT[metric.direction]}`}>
              {STATUS_WORD[metric.direction]}
              {metric.pct_vs_norm !== null
                ? ` ${metric.pct_vs_norm > 0 ? "+" : ""}${Math.round(metric.pct_vs_norm)}%`
                : ""}
            </span>
            <span className="text-gray-400 dark:text-gray-500">
              {" "}
              · typical {format(metric.norm as number)}
            </span>
          </div>
        </>
      )}

      <StatDiff label="vs prev" current={current} previous={previous} format={format} />
    </div>
  );
}
