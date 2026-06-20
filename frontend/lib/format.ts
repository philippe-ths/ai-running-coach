/**
 * Formatting utilities for running metrics.
 *
 * Extract reusable display logic here instead of inlining it in components.
 */

/**
 * The activity's own local wall-clock start as a Date (#399).
 *
 * Prefers start_date_local (Strava's local time, a naive "YYYY-MM-DDTHH:MM:SS"
 * string with no offset) and rebuilds it through the local Date constructor, so
 * the wall-clock numbers are preserved exactly regardless of the viewer's
 * timezone (a run logged at 09:00 always shows 09:00, even abroad). Falls back to
 * the UTC start_date (converted to the browser's zone) when no local time exists.
 */
export function activityStartDate(a: {
  start_date: string;
  start_date_local?: string | null;
}): Date {
  const local = a.start_date_local;
  if (local) {
    const [datePart, timePart = "00:00:00"] = local.replace("Z", "").split("T");
    const [y, m, d] = datePart.split("-").map(Number);
    const [hh, mm, ss] = timePart.split(":").map(Number);
    return new Date(y, (m || 1) - 1, d, hh || 0, mm || 0, ss || 0);
  }
  return new Date(a.start_date);
}

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

/**
 * Format a split duration as "m:ss" (e.g. "5:00", "4:59").
 *
 * Splits are short and near a round interval, so flooring to whole minutes
 * (formatDuration) misleadingly renders a 5:00 split as "4 min" / hides the
 * remainder of a partial split. The seconds precision keeps the value honest
 * and consistent with the "Splits (5 min)" title (#174).
 */
export function formatSplitDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  // A rounded-up 60 s belongs to the next minute.
  if (s === 60) return `${m + 1}:00`;
  return `${m}:${s.toString().padStart(2, "0")}`;
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
