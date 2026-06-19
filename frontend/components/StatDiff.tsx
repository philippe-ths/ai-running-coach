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
  /**
   * Optional neutral-coloured prefix (e.g. a zone name) so several deltas can be
   * told apart when stacked, as on the Zone-Load card.
   */
  label?: string;
}

export default function StatDiff({
  current,
  previous,
  format,
  lowerIsBetter = false,
  label,
}: StatDiffProps) {
  if (previous === undefined || previous === null) return null;

  const prefix = label ? (
    <span className="text-gray-500 dark:text-gray-400 font-normal">{label} </span>
  ) : null;

  const diff = current - previous;
  // Small epsilon so float noise doesn't render as a spurious change.
  if (Math.abs(diff) < 0.001) {
    return (
      <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
        {prefix}No change
      </div>
    );
  }

  const isIncrease = diff > 0;
  const isGood = lowerIsBetter ? !isIncrease : isIncrease;
  const color = isGood
    ? "text-green-600 dark:text-green-400"
    : "text-red-600 dark:text-red-400";
  const arrow = isIncrease ? "↑" : "↓";

  // Percentage change alongside the absolute amount. The arrow already conveys
  // direction, so the percentage is shown as a magnitude. Omitted when there is
  // no positive baseline to divide by (a 0 → N jump has no finite percentage).
  const pct = previous > 0 ? Math.round((Math.abs(diff) / previous) * 100) : null;

  return (
    <div className={`text-xs ${color} mt-1 font-medium`}>
      {prefix}{arrow} {format(Math.abs(diff))}
      {pct !== null && <span className="opacity-75"> · {pct}%</span>}
    </div>
  );
}
