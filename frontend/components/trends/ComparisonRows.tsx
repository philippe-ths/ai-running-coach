import type { VolumeMetricVsNorm } from "@/lib/types/volume";

function signed(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct}%`;
}

/**
 * #400: the two comparisons for a metric, in one consistent shape — each row is
 * "vs {reference}  {signed %} · {reference value}", monochrome. The vs-typical row
 * is omitted when there's no norm (thin history / All range); the vs-prev row is
 * omitted when there's no preceding period.
 */
export default function ComparisonRows({
  metric,
  current,
  previous,
  format,
}: {
  metric?: VolumeMetricVsNorm;
  current: number;
  previous?: number | null;
  format: (n: number) => string;
}) {
  const hasNorm = !!metric && metric.direction !== "no_norm" && metric.norm !== null;
  const prevPct =
    previous != null && previous > 0
      ? Math.round(((current - previous) / previous) * 100)
      : null;

  if (!hasNorm && prevPct === null) return null;

  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs tabular-nums">
      {hasNorm && metric && (
        <>
          <span className="text-gray-400 dark:text-gray-500">vs typical</span>
          <span className="text-gray-700 dark:text-gray-200">
            {signed(Math.round(metric.pct_vs_norm ?? 0))}
            <span className="text-gray-400 dark:text-gray-500">
              {" "}
              · {format(metric.norm as number)}
            </span>
          </span>
        </>
      )}
      {prevPct !== null && (
        <>
          <span className="text-gray-400 dark:text-gray-500">vs prev</span>
          <span className="text-gray-700 dark:text-gray-200">
            {signed(prevPct)}
            <span className="text-gray-400 dark:text-gray-500">
              {" "}
              · {format(previous as number)}
            </span>
          </span>
        </>
      )}
    </div>
  );
}
