// The profile edit payload, unchanged from the single-form page it replaces
// (#941). Every section screen edits a copy of this shape and saves the WHOLE
// object, because PUT /api/profile validates against UserProfileCreate, which
// requires goal_type, experience_level and weekly_days_available on every
// request -- a per-section partial body would be rejected before
// `exclude_unset` ever ran.

// #1068: a PB the runner tells us. `distance` uses Strava's best-effort labels so
// the backend can set it against the PBs it derives from their runs.
export type StatedPb = { distance: string; time_s: number; on: string | null };

export type ProfileForm = {
  goal_type: string;
  experience_level: string;
  weekly_days_available: number;
  current_weekly_km: number;
  injury_notes: string;
  upcoming_races: unknown[];
  max_hr: number;
  resting_hr: number;
  // #742: null, not 0. These post straight through to the coach pack, and the
  // backend rejects a physiologically impossible figure rather than coaching on
  // it -- so the other fields' 0-means-empty sentinel would be a 422 here.
  weight_kg: number | null;
  height_cm: number | null;
  week_starts_on: number; // 0=Monday (default), 6=Sunday (#676)
  max_activities_per_day: number | null; // #1080: null = no limit of their own
  stated_pbs: StatedPb[] | null; // #1068: null = none stated
};

export const EMPTY_PROFILE_FORM: ProfileForm = {
  goal_type: 'general',
  experience_level: 'intermediate',
  weekly_days_available: 4,
  current_weekly_km: 0,
  injury_notes: '',
  // upcoming_races is round-tripped, never edited here; the form has no UI for
  // it and dropping the key would leave the stored races unset on every save.
  upcoming_races: [],
  max_hr: 0,
  resting_hr: 0,
  weight_kg: null,
  height_cm: null,
  week_starts_on: 0,
  max_activities_per_day: null,
  stated_pbs: null,
};

// #742: clearing a body field must send null ("not stated"), never 0 -- the
// coach pack drops an unstated build rather than reading it as a real figure.
const NULLABLE_NUMERIC = ['weight_kg', 'height_cm', 'max_activities_per_day'];
const NUMERIC = [
  'weekly_days_available',
  'current_weekly_km',
  'max_hr',
  'resting_hr',
  'week_starts_on',
];

export function coerceField(name: string, value: string): string | number | null {
  if (NULLABLE_NUMERIC.includes(name)) return value === '' ? null : Number(value);
  if (NUMERIC.includes(name)) return Number(value);
  return value;
}

// Only the distances the PB screen can show: a row it cannot show is one the
// runner could neither see nor fix there.
function shownPbs(pbs: StatedPb[] | null): StatedPb[] | null {
  const shown = (pbs ?? []).filter((pb) => PB_DISTANCES.some((d) => d.key === pb.distance));
  return shown.length ? shown : null;
}

export function profileFromApi(data: Record<string, unknown> | null): ProfileForm {
  if (!data) return EMPTY_PROFILE_FORM;
  return {
    goal_type: (data.goal_type as string) || 'general',
    experience_level: (data.experience_level as string) || 'intermediate',
    weekly_days_available: (data.weekly_days_available as number) || 4,
    current_weekly_km: (data.current_weekly_km as number) || 0,
    injury_notes: (data.injury_notes as string) || '',
    upcoming_races: (data.upcoming_races as unknown[]) || [],
    max_hr: (data.max_hr as number) || 0,
    resting_hr: (data.resting_hr as number) || 0,
    weight_kg: (data.weight_kg as number | null) ?? null,
    height_cm: (data.height_cm as number | null) ?? null,
    week_starts_on: (data.week_starts_on as number) ?? 0,
    max_activities_per_day: (data.max_activities_per_day as number | null) ?? null,
    stated_pbs: shownPbs(data.stated_pbs as StatedPb[] | null),
  };
}

export const GOAL_LABELS: Record<string, string> = {
  general: 'General fitness',
  '5k': '5k',
  '10k': '10k',
  half: 'Half marathon',
  marathon: 'Marathon',
};

export const EXPERIENCE_LABELS: Record<string, string> = {
  new: 'Beginner',
  intermediate: 'Intermediate',
  advanced: 'Advanced',
};

// #1068: the distances a PB can be stated at, keyed by Strava's best-effort label.
// The bounds mirror the backend envelope (app/services/personal_bests.py): just
// under the world record to about 15 min/km. Outside them is a typo, not a PB.
export const PB_DISTANCES: { key: string; label: string; min: number; max: number }[] = [
  { key: '1 mile', label: '1 mile', min: 220, max: 1500 },
  { key: '5K', label: '5K', min: 750, max: 4500 },
  { key: '10K', label: '10K', min: 1570, max: 9000 },
  { key: 'Half-Marathon', label: 'Half marathon', min: 3450, max: 19000 },
  { key: 'Marathon', label: 'Marathon', min: 7200, max: 38000 },
];

// "22:30" or "1:42:10" -> seconds; null for anything else.
export function parseDuration(text: string): number | null {
  const parts = text.trim().split(':');
  if (parts.length < 2 || parts.length > 3) return null;
  if (!parts.every((p) => /^\d+$/.test(p))) return null;
  const nums = parts.map(Number);
  if (nums.slice(1).some((n) => n >= 60)) return null;
  return nums.reduce((total, n) => total * 60 + n, 0);
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// #1068: whether stored PBs would pass the backend's checks, so their screen
// can hold Save from the moment it opens rather than only after an edit.
export function storedPbsValid(pbs: StatedPb[] | null, today: string): boolean {
  return (pbs ?? []).every((pb) => {
    const d = PB_DISTANCES.find((x) => x.key === pb.distance);
    return Boolean(d) && pb.time_s >= d!.min && pb.time_s <= d!.max && !(pb.on && pb.on > today);
  });
}
