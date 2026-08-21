'use client';

import { ReactNode } from 'react';
import { ChipGroup, CountPicker, FieldHint, NumberField, SegmentedControl } from './controls';
import { ProfileForm } from './profileForm';

// The screens that edit profile fields (#941). Each renders a slice of the one
// ProfileForm the page owns; none of them saves -- the page does, with the
// whole object, because the backend requires goal_type, experience_level and
// weekly_days_available on every PUT.

type SectionProps = {
  form: ProfileForm;
  onChange: (name: keyof ProfileForm, value: string) => void;
};

const CARD =
  'rounded-xl border bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800';

export function TrainingSection({ form, onChange }: SectionProps) {
  return (
    <div className={`${CARD} space-y-6`}>
      <ChipGroup
        legend="What you're training for"
        name="goal_type"
        value={form.goal_type}
        onChange={(v) => onChange('goal_type', v)}
        options={[
          { value: '5k', label: '5k' },
          { value: '10k', label: '10k' },
          { value: 'half', label: 'Half marathon' },
          { value: 'marathon', label: 'Marathon' },
          { value: 'general', label: 'General fitness' },
        ]}
      />

      <SegmentedControl
        legend="Experience"
        name="experience_level"
        value={form.experience_level}
        onChange={(v) => onChange('experience_level', v)}
        options={[
          { value: 'new', label: 'Beginner' },
          { value: 'intermediate', label: 'Intermediate' },
          { value: 'advanced', label: 'Advanced' },
        ]}
      />

      <CountPicker
        legend="Days you can run each week"
        name="weekly_days_available"
        max={7}
        value={form.weekly_days_available}
        onChange={(v) => onChange('weekly_days_available', v)}
      />

      <div className="space-y-1">
        <NumberField
          id="current_weekly_km"
          label="Current weekly volume (km)"
          unit="km"
          min="0"
          value={form.current_weekly_km || ''}
          onChange={(v) => onChange('current_weekly_km', v)}
          hintId="current_weekly_km-hint"
        />
        <FieldHint id="current_weekly_km-hint">
          Roughly what you are running now, so the coach ramps from the right place.
        </FieldHint>
      </div>
    </div>
  );
}

export function BodySection({ form, onChange }: SectionProps) {
  return (
    <div className={`${CARD} space-y-5`}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          id="max_hr"
          label="Max heart rate (bpm)"
          unit="bpm"
          min="100"
          max="250"
          placeholder="e.g. 190"
          value={form.max_hr || ''}
          onChange={(v) => onChange('max_hr', v)}
          hintId="body-hint"
        />
        <NumberField
          id="resting_hr"
          label="Resting heart rate (bpm)"
          unit="bpm"
          min="30"
          max="120"
          placeholder="e.g. 50"
          value={form.resting_hr || ''}
          onChange={(v) => onChange('resting_hr', v)}
          hintId="body-hint"
        />
        <NumberField
          id="weight_kg"
          label="Weight (kg)"
          unit="kg"
          min="20"
          max="300"
          step="0.1"
          placeholder="e.g. 72.5"
          value={form.weight_kg ?? ''}
          onChange={(v) => onChange('weight_kg', v)}
          hintId="body-hint"
        />
        <NumberField
          id="height_cm"
          label="Height (cm)"
          unit="cm"
          min="100"
          max="250"
          step="0.5"
          placeholder="e.g. 178"
          value={form.height_cm ?? ''}
          onChange={(v) => onChange('height_cm', v)}
          hintId="body-hint"
        />
      </div>

      {/* One note for the group. The flat form carried three separate hints
          under three of the fields and none under the fourth, which is what
          made its two-column layout ragged. */}
      <div className="rounded-lg border bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-900">
        <FieldHint id="body-hint">
          Your heart rates set your training zones — if you do not know your max,
          estimate it with 220 minus your age. Weight and height only tell the coach
          how fast to ramp volume and how much strength work to prescribe, never a
          target weight. Leave them blank and they simply are not considered.
        </FieldHint>
      </div>
    </div>
  );
}

export function HealthSection({ form, onChange }: SectionProps) {
  return (
    <div className={CARD}>
      <label htmlFor="injury_notes" className="block text-sm font-medium mb-1">
        Injury / health notes
      </label>
      <textarea
        id="injury_notes"
        name="injury_notes"
        value={form.injury_notes}
        onChange={(e) => onChange('injury_notes', e.target.value)}
        rows={6}
        placeholder="Anything nagging, or an old injury the coach should work around?"
        aria-describedby="injury_notes-hint"
        className="w-full rounded-lg border border-gray-300 bg-white p-3 text-base outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
      />
      <FieldHint id="injury_notes-hint">
        The coach reads this before it prescribes. It is not medical advice and it
        does not replace seeing someone about a persistent pain.
      </FieldHint>
    </div>
  );
}

export function AppSection({
  form,
  onChange,
  themeControl,
}: SectionProps & { themeControl: ReactNode }) {
  return (
    <div className="space-y-4">
      <div className={`${CARD} space-y-1`}>
        <SegmentedControl
          legend="Your week starts on"
          name="week_starts_on"
          value={form.week_starts_on}
          onChange={(v) => onChange('week_starts_on', v)}
          hintId="week_starts_on-hint"
          options={[
            { value: 0, label: 'Monday' },
            { value: 6, label: 'Sunday' },
          ]}
        />
        <FieldHint id="week_starts_on-hint">
          Sets which day your training week begins, for “this week” on the coach,
          Load and Trends.
        </FieldHint>
      </div>

      {/* Appearance is a device preference, not profile data: it saves itself
          the moment you pick it and is not part of the profile's Save. */}
      <div className={CARD}>
        <h2 className="text-sm font-medium mb-2">Appearance</h2>
        {themeControl}
        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
          Applies to this device straight away.
        </p>
      </div>
    </div>
  );
}
