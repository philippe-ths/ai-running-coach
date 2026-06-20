import { DIR_TEXT, dirFromPct } from "./direction";
import InfoPopover from "./InfoPopover";
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
  prevLabel = "vs prev",
  typicalHelp,
  prevHelp,
}: {
  metric?: VolumeMetricVsNorm;
  current: number;
  previous?: number | null;
  format: (n: number) => string;
  /** Label for the period-over-period row. Calendar mode names the period
   * (e.g. "vs last month"); rolling stays "vs prev" (#413). */
  prevLabel?: string;
  /** When set, a tap-to-reveal (i) explains what the "vs typical" norm is based on. */
  typicalHelp?: string;
  /** When set, a tap-to-reveal (i) explains what the period-over-period row compares. */
  prevHelp?: string;
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
          <span className="inline-flex items-center gap-1 text-gray-400 dark:text-gray-500">
            vs typical
            {typicalHelp && (
              <InfoPopover label="What does vs typical mean?" text={typicalHelp} />
            )}
          </span>
          <span className="text-gray-400 dark:text-gray-500">
            <span
              className={`font-medium ${DIR_TEXT[dirFromPct(Math.round(metric.pct_vs_norm ?? 0))]}`}
            >
              {signed(Math.round(metric.pct_vs_norm ?? 0))}
            </span>{" "}
            · <span className="whitespace-nowrap">{format(metric.norm as number)}</span>
          </span>
        </>
      )}
      {prevPct !== null && (
        <>
          <span className="inline-flex items-center gap-1 text-gray-400 dark:text-gray-500">
            {prevLabel}
            {prevHelp && (
              <InfoPopover label={`What does ${prevLabel} mean?`} text={prevHelp} />
            )}
          </span>
          <span className="text-gray-400 dark:text-gray-500">
            <span className={`font-medium ${DIR_TEXT[dirFromPct(prevPct)]}`}>
              {signed(prevPct)}
            </span>{" "}
            · <span className="whitespace-nowrap">{format(previous as number)}</span>
          </span>
        </>
      )}
    </div>
  );
}
