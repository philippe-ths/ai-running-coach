import { Lap, Split } from "@/lib/types/activity";

// A full auto-lap is treated as "~1 km" within this tolerance. Device
// distance auto-laps report a clean 1000 m; the band absorbs minor GPS
// rounding without admitting interval reps (200-500 m) or mile laps.
const KM_LOW = 940;
const KM_HIGH = 1060;

/**
 * Whether the recorded laps are "just" per-kilometre auto-distance laps,
 * i.e. the same breakdown the per-km splits already show (#562).
 *
 * When a device auto-laps every 1 km, the Laps and Splits cards display the
 * same distances and paces twice. In that case the laps carry no intent the
 * splits can't reconstruct, so the page collapses to a single view. When the
 * runner pressed the lap button or ran a structured/planned workout, the laps
 * diverge from the per-km splits (varied distances, different count) and both
 * views stay available because the lap structure then carries intent.
 *
 * The signal is structural rather than a Strava "manual" flag (which the lap
 * objects do not reliably carry): the laps must align 1:1 with distance-based
 * per-km splits (count within 1, to allow a tiny trailing partial the split
 * builder may merge) and every full lap must be ~1 km. Validated against real
 * lap data, including interval sessions the backend interval detector does not
 * tag as recorded_laps.
 */
export function lapsAreAutoDistance(
  laps?: Lap[] | null,
  splits?: Split[] | null,
): boolean {
  if (!laps || laps.length < 2) return false;
  if (!splits || splits.length === 0) return false;
  // Only per-km (distance) splits can be the duplicate of distance auto-laps.
  if (splits[0]?.split_type !== "distance") return false;
  // Lap count must track the per-km split count (a tiny final partial lap may
  // be merged into the last split, so allow a difference of one).
  if (Math.abs(laps.length - splits.length) > 1) return false;
  // Every lap but the (possibly partial) last must be ~1 km.
  const fullLaps = laps.slice(0, -1);
  if (fullLaps.length === 0) return false;
  return fullLaps.every(
    (l) => l.distance_m != null && l.distance_m >= KM_LOW && l.distance_m <= KM_HIGH,
  );
}
