/**
 * Formatting utilities for running metrics.
 *
 * Extract reusable display logic here instead of inlining it in components.
 */

/** Format a pace in min:ss /km given metres and seconds. */
export function formatPace(distanceM: number, timeS: number): string {
  const mps = distanceM / timeS;
  if (!mps || !isFinite(mps)) return "-";
  const secPerKm = 1000 / mps;
  const min = Math.floor(secPerKm / 60);
  const sec = Math.round(secPerKm % 60);
  return `${min}:${sec.toString().padStart(2, "0")} /km`;
}

/** Format seconds as "Xh Ym" or "Xm". */
export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m} min`;
}

/** Format metres to km with two decimal places. */
export function formatDistanceKm(metres: number): string {
  return `${(metres / 1000).toFixed(2)} km`;
}

/**
 * Format an ISO date string ("2026-02-08") to "Feb 8" without
 * timezone conversion.  parseISO creates midnight-UTC which shifts
 * the displayed date when the browser is behind UTC.
 */
export function formatDateLabel(iso: string): string {
  const MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const [, m, d] = iso.split("-");
  return `${MONTHS[parseInt(m, 10) - 1]} ${parseInt(d, 10)}`;
}
