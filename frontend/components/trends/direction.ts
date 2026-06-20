import type { VolumeDirection } from "@/lib/types/volume";

// Calm, non-judgmental directional colours: a temperature pair (warm = up/more,
// cool = down/less), deliberately NOT red/green — a down window is not "bad".
// in-line / unknown stays neutral grey.
export const DIR_TEXT: Record<string, string> = {
  up: "text-amber-600 dark:text-amber-400",
  down: "text-sky-600 dark:text-sky-400",
  in_line: "text-gray-500 dark:text-gray-400",
  no_norm: "text-gray-500 dark:text-gray-400",
};

export const DIR_FILL: Record<string, string> = {
  up: "bg-amber-500 dark:bg-amber-400",
  down: "bg-sky-500 dark:bg-sky-400",
  in_line: "bg-gray-400 dark:bg-gray-300",
  no_norm: "bg-gray-400 dark:bg-gray-300",
};

/** Direction of a raw signed delta (no deadband — used for vs-prev). */
export function dirFromPct(pct: number): VolumeDirection {
  if (pct > 0) return "up";
  if (pct < 0) return "down";
  return "in_line";
}
