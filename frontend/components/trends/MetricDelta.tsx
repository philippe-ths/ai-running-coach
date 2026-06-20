import StatDiff from "@/components/StatDiff";
import NormStat from "./NormStat";
import type { VolumeMetricVsNorm } from "@/lib/types/volume";

/**
 * #400: a metric's two comparisons stacked — vs the runner's norm (when known)
 * and the straight delta vs the preceding term. Used on the Trends quick-view
 * cards and the matching chart headers so both framings show together.
 */
export default function MetricDelta({
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
  return (
    <>
      <NormStat metric={metric} format={format} />
      <StatDiff label="vs prev" current={current} previous={previous} format={format} />
    </>
  );
}
