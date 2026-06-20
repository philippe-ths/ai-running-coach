import type { VolumeDirection } from "@/lib/types/volume";

// The track spans ±SCALE% around "typical" (center). DEADBAND is the in-line zone
// (matches the backend's deadband), drawn as a faint band so "inside the band =
// normal" reads at a glance.
const SCALE = 50;
const DEADBAND = 15;

function fillColor(d: VolumeDirection): string {
  if (d === "down") return "bg-sky-400";
  if (d === "up") return "bg-amber-400";
  return "bg-slate-400 dark:bg-slate-300"; // in_line
}

/**
 * #400: a deviation-from-typical gauge. The center tick is the runner's norm for
 * this window; the marker shows where the current period landed, and the faint
 * band is the in-line zone. Reads "in your usual band / above / below" instantly,
 * across any unit.
 */
export default function NormGauge({
  pct,
  direction,
}: {
  pct: number;
  direction: VolumeDirection;
}) {
  const clamped = Math.max(-SCALE, Math.min(SCALE, pct));
  const pos = 50 + (clamped / SCALE) * 50; // 0..100 along the track
  const bandInset = 100 - (50 + (DEADBAND / SCALE) * 50); // 35 -> inset 35% each side
  const fillLeft = Math.min(50, pos);
  const fillWidth = Math.abs(pos - 50);
  const c = fillColor(direction);

  return (
    <div
      className="relative my-2.5 h-2 rounded-full bg-gray-200 dark:bg-gray-700/70"
      role="img"
      aria-label={`${Math.round(pct)} percent versus your typical`}
    >
      {/* in-line band (the "normal" zone) — clearly lighter than the track */}
      <div
        className="absolute inset-y-0 rounded-full bg-gray-300 dark:bg-gray-600"
        style={{ left: `${bandInset}%`, right: `${bandInset}%` }}
      />
      {/* diverging fill from the typical tick to the current marker */}
      <div
        className={`absolute inset-y-0 rounded-full ${c}`}
        style={{ left: `${fillLeft}%`, width: `${fillWidth}%` }}
      />
      {/* "typical" tick at center */}
      <div
        className="absolute -top-1.5 -bottom-1.5 w-0.5 -translate-x-1/2 rounded-full bg-gray-500 dark:bg-gray-200"
        style={{ left: "50%" }}
      />
      {/* current marker */}
      <div
        className={`absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ${c} ring-2 ring-white dark:ring-gray-800`}
        style={{ left: `${pos}%` }}
      />
    </div>
  );
}
