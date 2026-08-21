// The profile edit payload, unchanged from the single-form page it replaces
// (#941). Every section screen edits a copy of this shape and saves the WHOLE
// object, because PUT /api/profile validates against UserProfileCreate, which
// requires goal_type, experience_level and weekly_days_available on every
// request -- a per-section partial body would be rejected before
// `exclude_unset` ever ran.

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
};

// #742: clearing a body field must send null ("not stated"), never 0 -- the
// coach pack drops an unstated build rather than reading it as a real figure.
const NULLABLE_NUMERIC = ['weight_kg', 'height_cm'];
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
