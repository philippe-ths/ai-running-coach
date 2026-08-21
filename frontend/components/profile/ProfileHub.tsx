'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useTheme } from 'next-themes';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import { SettingGroup, SettingRow } from './SettingRow';
import { useConnectionStatus } from '@/lib/useConnectionStatus';
import {
  EXPERIENCE_LABELS,
  GOAL_LABELS,
  ProfileForm,
} from './profileForm';

// The profile hub (#941): what the coach knows about you, stated as values you
// can read without touching a control. Every row links to the screen that edits
// it. Nothing here writes.

function summaryLine(form: ProfileForm): string {
  const parts = [
    GOAL_LABELS[form.goal_type] ?? form.goal_type,
    EXPERIENCE_LABELS[form.experience_level] ?? form.experience_level,
    `${form.weekly_days_available} days a week`,
  ];
  return parts.join(' · ');
}

function heartRateValue(form: ProfileForm): string {
  if (!form.max_hr && !form.resting_hr) return 'Not set';
  return `${form.max_hr || '—'} / ${form.resting_hr || '—'} bpm`;
}

function buildValue(form: ProfileForm): string {
  const parts: string[] = [];
  if (form.weight_kg != null) parts.push(`${form.weight_kg} kg`);
  if (form.height_cm != null) parts.push(`${form.height_cm} cm`);
  // #742: not stated is a real state, distinct from a figure of zero.
  return parts.length ? parts.join(' · ') : 'Not stated';
}

// One chip per external account, stating linked/not rather than offering the
// control. The controls live on the connections screen this links to.
function ConnectionChip({
  label,
  state,
}: {
  label: string;
  state: 'on' | 'off' | 'unknown';
}) {
  return (
    <span className="flex flex-1 items-center gap-2 rounded-lg border bg-white px-3 py-2.5 dark:border-gray-700 dark:bg-gray-800">
      {state === 'on' ? (
        <CheckCircle2
          size={16}
          aria-hidden="true"
          className="shrink-0 text-green-600 dark:text-green-400"
        />
      ) : (
        <AlertCircle
          size={16}
          aria-hidden="true"
          className="shrink-0 text-gray-400 dark:text-gray-500"
        />
      )}
      <span className="truncate text-sm font-medium">{label}</span>
      <span className="ml-auto shrink-0 text-xs text-gray-500 dark:text-gray-400">
        {state === 'on' ? 'Linked' : state === 'off' ? 'Not linked' : '…'}
      </span>
    </span>
  );
}

const THEME_LABELS: Record<string, string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
};

export default function ProfileHub({ form }: { form: ProfileForm }) {
  const { strava, telegram } = useConnectionStatus();

  // next-themes only knows the real theme after hydration, so the row shows a
  // neutral placeholder until then rather than guessing and flipping.
  const { theme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const themeValue = mounted ? (THEME_LABELS[theme ?? 'system'] ?? 'System') : '—';

  const stravaState = strava ? (strava.connected ? 'on' : 'off') : 'unknown';
  const telegramState = telegram ? (telegram.linked ? 'on' : 'off') : 'unknown';

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Athlete Profile</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {summaryLine(form)}
        </p>
      </header>

      <Link
        href="/profile?s=connections"
        aria-label="Connected accounts"
        className="flex gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <ConnectionChip label="Strava" state={stravaState} />
        {/* Bot not configured: the row is meaningless, so it is not shown at
            all -- the same rule LinkTelegramButton applies to itself. */}
        {telegram?.configured !== false && (
          <ConnectionChip label="Telegram" state={telegramState} />
        )}
      </Link>

      <SettingGroup title="Training">
        <SettingRow
          href="/profile?s=training"
          name="Goal"
          value={GOAL_LABELS[form.goal_type] ?? form.goal_type}
        />
        <SettingRow
          href="/profile?s=training"
          name="Experience"
          value={EXPERIENCE_LABELS[form.experience_level] ?? form.experience_level}
        />
        <SettingRow
          href="/profile?s=training"
          name="Days per week"
          value={String(form.weekly_days_available)}
          mono
        />
        <SettingRow
          href="/profile?s=training"
          name="Weekly volume"
          value={form.current_weekly_km ? `${form.current_weekly_km} km` : 'Not set'}
          valueTone={form.current_weekly_km ? 'default' : 'attention'}
          mono={Boolean(form.current_weekly_km)}
        />
      </SettingGroup>

      <SettingGroup title="You">
        <SettingRow
          href="/profile?s=body"
          name="Heart rate"
          value={heartRateValue(form)}
          mono={Boolean(form.max_hr || form.resting_hr)}
        />
        <SettingRow
          href="/profile?s=body"
          name="Build"
          value={buildValue(form)}
          valueTone={form.weight_kg == null && form.height_cm == null ? 'muted' : 'default'}
          mono={form.weight_kg != null || form.height_cm != null}
        />
        <SettingRow
          href="/profile?s=health"
          name="Injuries & health"
          value={form.injury_notes.trim() ? 'Noted' : 'Nothing noted'}
          valueTone={form.injury_notes.trim() ? 'default' : 'muted'}
        />
      </SettingGroup>

      {/* These three save independently of the profile, which is why they sit
          in their own group and their own screens rather than under the
          profile's Save. */}
      <SettingGroup title="Your coach">
        <SettingRow
          href="/profile?s=voice"
          name="Voice"
          value="How it sounds"
          valueTone="muted"
        />
        <SettingRow
          href="/profile?s=stance"
          name="Stance"
          value="What it emphasises"
          valueTone="muted"
        />
        <SettingRow
          href="/profile?s=materials"
          name="Coaching materials"
          value="Your uploads"
          valueTone="muted"
        />
      </SettingGroup>

      <SettingGroup title="App">
        <SettingRow
          href="/profile?s=app"
          name="Week starts on"
          value={form.week_starts_on === 6 ? 'Sunday' : 'Monday'}
        />
        <SettingRow href="/profile?s=app" name="Appearance" value={themeValue} />
      </SettingGroup>
    </div>
  );
}
