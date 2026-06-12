/**
 * Week-over-week (or period-over-period) delta shown under a summary card.
 *
 * Shared by the Trends summary cards and the Dashboard weekly-summary cards so
 * the two surfaces render the comparison identically (#248). Renders nothing
 * when there is no previous value to compare against.
 */

interface StatDiffProps {
  current: number;
  previous?: number | null;
  format: (val: number) => string;
  /**
   * When true, a decrease is "good" (green) and an increase is "bad" (red).
   * Defaults to false — for distance/time/count/load, more is treated as active.
   */
  lowerIsBetter?: boolean;
}

export default function StatDiff({
  current,
  previous,
  format,
  lowerIsBetter = false,
}: StatDiffProps) {
  if (previous === undefined || previous === null) return null;

  const diff = current - previous;
  // Small epsilon so float noise doesn't render as a spurious change.
  if (Math.abs(diff) < 0.001) {
    return <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">No change</div>;
  }

  const isIncrease = diff > 0;
  const isGood = lowerIsBetter ? !isIncrease : isIncrease;
  const color = isGood
    ? "text-green-600 dark:text-green-400"
    : "text-red-600 dark:text-red-400";
  const arrow = isIncrease ? "↑" : "↓";

  return (
    <div className={`text-xs ${color} mt-1 font-medium`}>
      {arrow} {format(Math.abs(diff))}
    </div>
  );
}
