import { TrainingLoad } from '@/lib/types';

// P3 readiness card (ADR 0016): the runner's current-condition read for this
// activity — fitness / fatigue / form + a condition label. Honest low-confidence
// state while the chronic baseline is still warming up. UX is owner-judged.

const CONDITION_META: Record<
  string,
  { label: string; blurb: string; cls: string }
> = {
  fresh: {
    label: 'Fresh',
    blurb: 'Fitness outweighs fatigue — your legs are rested.',
    cls: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200',
  },
  balanced: {
    label: 'Balanced',
    blurb: 'Productive training — fatigue and fitness are in step.',
    cls: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200',
  },
  fatigued: {
    label: 'Fatigued',
    blurb: 'Carrying notable fatigue against your fitness.',
    cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  },
  overreaching: {
    label: 'Overreaching',
    blurb: 'Fatigue is well ahead of fitness — watch recovery.',
    cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200',
  },
  building_baseline: {
    label: 'Building baseline',
    blurb: 'Not enough history yet to read your condition confidently.',
    cls: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
  },
};

const TREND_LABEL: Record<string, string> = {
  building: 'Fitness building',
  steady: 'Fitness steady',
  detraining: 'Fitness slipping',
};

function Stat({
  label,
  value,
  hint,
  signed,
}: {
  label: string;
  value: number;
  hint: string;
  signed?: boolean;
}) {
  const display = signed && value > 0 ? `+${value}` : `${value}`;
  return (
    <div>
      <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">{display}</div>
      <div className="text-xs font-medium text-gray-600 dark:text-gray-300">{label}</div>
      <div className="text-[10px] text-gray-400 dark:text-gray-500">{hint}</div>
    </div>
  );
}

export default function TrainingLoadCard({ data }: { data: TrainingLoad }) {
  const meta = CONDITION_META[data.condition] ?? CONDITION_META.building_baseline;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-gray-700 dark:text-gray-300">Training Load</h3>
        <span className={`inline-block px-2 py-0.5 rounded-full text-sm font-medium ${meta.cls}`}>
          {meta.label}
        </span>
      </div>
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">{meta.blurb}</p>

      <div className="grid grid-cols-3 gap-3 text-center">
        <Stat label="Fitness" value={data.fitness} hint="chronic load (42d)" />
        <Stat label="Fatigue" value={data.fatigue} hint="acute load (7d)" />
        <Stat label="Form" value={data.form} hint="fitness − fatigue" signed />
      </div>

      {!data.warming_up && (
        <div className="mt-4 flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
          <span>{TREND_LABEL[data.trend] ?? 'Fitness steady'}</span>
          {data.ramp_aggressive && (
            <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
              ramping fast
            </span>
          )}
        </div>
      )}

      {data.warming_up && (
        <p className="mt-4 text-xs text-gray-400 dark:text-gray-500">
          Based on {data.sample_count} recent {data.sample_count === 1 ? 'activity' : 'activities'} — still establishing your baseline.
        </p>
      )}

      <p className="mt-3 text-[10px] text-gray-400 dark:text-gray-500">
        Load in zone-minutes — our own read of current condition, not a Strava/Garmin number.
      </p>
    </div>
  );
}
